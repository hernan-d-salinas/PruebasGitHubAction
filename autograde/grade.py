# autograde/grade.py
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from common import (
    GradeResult,
    extract_lab_summary,
    find_markdown_section_text,
    get_student_from_path,
    is_number,
    read_notebook,
    require_keys,
    require_list_len,
    require_number_range,
    safe_json_dump,
)

THIS_DIR = Path(__file__).resolve().parent


def load_spec(lab: str) -> Dict[str, Any]:
    spec_path = THIS_DIR / "specs" / f"{lab}.json"
    if not spec_path.exists():
        raise FileNotFoundError(f"No existe spec para {lab}: {spec_path}")
    return json.loads(spec_path.read_text(encoding="utf-8"))


def count_words(text: str) -> int:
    if not text:
        return 0
    return len([w for w in text.replace("\n", " ").split(" ") if w.strip()])


def validate_lab01(summary: Dict[str, Any], spec: Dict[str, Any], result: GradeResult):
    # Parse error
    if summary is None:
        result.status = "FAIL"
        result.score = 0.0
        result.errors.append("No se encontró LAB_SUMMARY en el notebook.")
        return
    if summary.get("__parse_error__"):
        result.status = "FAIL"
        result.score = 0.0
        result.errors.append("LAB_SUMMARY existe pero no pude parsearlo. Usa un dict literal bien formado.")
        return

    # Required keys
    req = spec.get("required_keys", [])
    result.errors.extend(require_keys(summary, req))

    # Ranges
    rules = spec.get("rules", {})
    pr = rules.get("pvalue_range", [0.0, 1.0])
    ar = rules.get("alpha_range", [0.0, 1.0])

    err = require_number_range(summary, "pvalue", float(pr[0]), float(pr[1]))
    if err: result.errors.append(err)

    err = require_number_range(summary, "alpha", float(ar[0]), float(ar[1]))
    if err: result.errors.append(err)

    # clean_shape
    if "clean_shape" in summary:
        sh = summary["clean_shape"]
        if not isinstance(sh, (list, tuple)) or len(sh) != 2:
            result.errors.append("'clean_shape' debe ser [n_filas, n_cols].")
        else:
            nrows = sh[0]
            if not is_number(nrows):
                result.errors.append("'clean_shape[0]' debe ser numérico.")
            else:
                min_rows = int(rules.get("min_rows_clean", 1))
                if int(nrows) < min_rows:
                    result.warnings.append(f"clean_shape tiene pocas filas ({nrows}). ¿Limpieza excesiva?")

    # effect: dict con name/value
    if "effect" in summary:
        eff = summary["effect"]
        if not isinstance(eff, dict) or "name" not in eff or "value" not in eff:
            result.errors.append("effect debe ser dict con {'name':..., 'value':...}.")
        else:
            if not is_number(eff["value"]):
                result.errors.append("effect['value'] debe ser numérico.")

    # CI
    if "bootstrap_ci_diff" in summary:
        ci = summary["bootstrap_ci_diff"]
        if not isinstance(ci, (list, tuple)) or len(ci) != 2:
            result.errors.append("bootstrap_ci_diff debe ser [lo, hi].")
        else:
            lo, hi = ci[0], ci[1]
            if not (is_number(lo) and is_number(hi)):
                result.errors.append("bootstrap_ci_diff debe contener números.")
            else:
                if rules.get("ci_requires_order", True) and float(lo) >= float(hi):
                    result.errors.append("bootstrap_ci_diff inválido: lo >= hi.")

    # dashboard spec
    if "dashboard" in summary:
        dash = summary["dashboard"]
        if not isinstance(dash, dict):
            result.errors.append("dashboard debe ser un dict.")
        else:
            # filters
            filters_req = set(rules.get("dashboard_filters_required", []))
            filters = dash.get("filters", [])
            if not isinstance(filters, list):
                result.errors.append("dashboard['filters'] debe ser lista.")
            else:
                missing = filters_req - set(filters)
                if missing:
                    result.warnings.append(f"dashboard.filters no incluye: {sorted(missing)}")

            charts = dash.get("charts", [])
            if not isinstance(charts, list):
                result.errors.append("dashboard['charts'] debe ser lista.")
            else:
                min_charts = int(rules.get("dashboard_charts_min", 0))
                if len(charts) < min_charts:
                    result.warnings.append(f"dashboard.charts tiene menos de {min_charts} gráficos.")

    # Consistencia student vs carpeta
    if "student" in summary and isinstance(summary["student"], str):
        if summary["student"].strip().lower() != result.student.strip().lower():
            result.warnings.append(
                f"LAB_SUMMARY['student']='{summary['student']}' no coincide con carpeta '{result.student}'."
            )

    # Report mínimo (opcional)
    report_spec = spec.get("report", {})
    title_rx = report_spec.get("require_markdown_title_regex")
    min_words = int(report_spec.get("min_words_in_report_cell", 0))
    if title_rx and min_words > 0:
        # Esto lo validamos fuera (porque requiere notebook). Aquí solo chequea si el campo existe.
        pass

    # Final status/score simple
    if result.errors:
        result.status = "FAIL"
        result.score = 0.0
    else:
        result.status = "PASS"
        # score simple: penaliza warnings un poco
        w = len(result.warnings)
        result.score = max(0.7, 1.0 - 0.05 * w)


def validate_report(nb, spec: Dict[str, Any], result: GradeResult):
    report_spec = spec.get("report", {})
    title_rx = report_spec.get("require_markdown_title_regex")
    min_words = int(report_spec.get("min_words_in_report_cell", 0))
    if not title_rx or min_words <= 0:
        return

    text = find_markdown_section_text(nb, title_regex=title_rx)
    wc = count_words(text)
    if wc < min_words:
        result.warnings.append(
            f"Mini-reporte muy corto o ausente (palabras={wc}, mínimo={min_words})."
        )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lab", required=True, help="Ej: lab01")
    ap.add_argument("--paths", required=True, help="Rutas separadas por espacio o una sola ruta.")
    ap.add_argument("--out", default="grades.json")
    args = ap.parse_args()

    lab = args.lab.strip()
    spec = load_spec(lab)

    # paths puede venir con espacios (una string). Separamos de forma simple.
    paths = [p for p in args.paths.split(" ") if p.strip()]
    nb_paths = [Path(p) for p in paths]

    results: List[Dict[str, Any]] = []
    any_fail = False

    for nb_path in nb_paths:
        student = get_student_from_path(nb_path)
        gr = GradeResult(student=student, path=str(nb_path), lab=lab)

        try:
            nb = read_notebook(nb_path)
        except Exception as e:
            gr.status = "FAIL"
            gr.score = 0.0
            gr.errors.append(f"No pude leer el notebook: {e}")
            results.append(gr.__dict__)
            any_fail = True
            continue

        summary_var = spec.get("summary_var", "LAB_SUMMARY")
        summary = extract_lab_summary(nb, varname=summary_var)

        # Validación por lab (por ahora implementamos lab01)
        if lab == "lab01":
            validate_lab01(summary, spec, gr)
            validate_report(nb, spec, gr)
        else:
            gr.status = "FAIL"
            gr.score = 0.0
            gr.errors.append(f"No hay validador implementado para {lab} (solo lab01 por ahora).")

        results.append(gr.__dict__)
        if gr.status == "FAIL":
            any_fail = True

    out_obj = {"lab": lab, "results": results}
    safe_json_dump(out_obj, Path(args.out))

    if any_fail:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
