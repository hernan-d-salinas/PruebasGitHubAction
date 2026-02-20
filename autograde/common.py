
# autograde/common.py
from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import nbformat


LAB_SUMMARY_VAR = "LAB_SUMMARY"


@dataclass
class GradeResult:
    student: str
    path: str
    lab: str
    status: str = "PASS"  # PASS/FAIL
    score: float = 1.0    # 0..1 (simple)
    errors: List[str] = None
    warnings: List[str] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []
        if self.warnings is None:
            self.warnings = []


def read_notebook(nb_path: Path) -> nbformat.NotebookNode:
    return nbformat.read(str(nb_path), as_version=4)


def _extract_dict_literal_from_code(code: str, varname: str) -> Optional[Dict[str, Any]]:
    """
    Busca: VAR = { ... } y parsea el dict con ast.literal_eval (sin ejecutar el notebook).
    Recomendación: que el estudiante deje LAB_SUMMARY como dict literal (no construido por código complejo).
    """
    # Captura greedy del dict. Funciona bien si el dict es literal estándar.
    pattern = re.compile(rf"{re.escape(varname)}\s*=\s*({{.*}})\s*$", re.DOTALL | re.MULTILINE)
    m = pattern.search(code)
    if not m:
        return None
    raw = m.group(1)
    try:
        return ast.literal_eval(raw)
    except Exception:
        # Intento alternativo: si hay trailing spaces/prints, buscar primera llave balanceada
        try:
            start = raw.find("{")
            if start < 0:
                return None
            # Búsqueda balanceada simple
            depth = 0
            end = None
            for i, ch in enumerate(raw[start:], start=start):
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
            if end is None:
                return None
            return ast.literal_eval(raw[start:end])
        except Exception:
            return {"__parse_error__": True}


def extract_lab_summary(nb: nbformat.NotebookNode, varname: str = LAB_SUMMARY_VAR) -> Optional[Dict[str, Any]]:
    for cell in nb.cells:
        if cell.get("cell_type") != "code":
            continue
        code = cell.get("source", "")
        d = _extract_dict_literal_from_code(code, varname=varname)
        if d is not None:
            return d
    return None


def get_student_from_path(nb_path: Path) -> str:
    # estudiantes/Apellido/lab01.ipynb -> Apellido
    return nb_path.parent.name


def find_markdown_section_text(nb: nbformat.NotebookNode, title_regex: str) -> str:
    """
    Devuelve el texto (concatenado) de celdas Markdown que contengan un título que matchee title_regex.
    Útil para exigir que el mini-reporte exista.
    """
    rx = re.compile(title_regex, re.IGNORECASE)
    texts = []
    for cell in nb.cells:
        if cell.get("cell_type") != "markdown":
            continue
        src = cell.get("source", "")
        if rx.search(src):
            texts.append(src)
    return "\n\n".join(texts).strip()


# ----------------------------
# Validadores genéricos
# ----------------------------

def is_number(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def require_keys(d: Dict[str, Any], keys: List[str]) -> List[str]:
    errs = []
    for k in keys:
        if k not in d:
            errs.append(f"Falta clave obligatoria: '{k}'")
    return errs


def require_type(d: Dict[str, Any], key: str, typ, msg: str) -> Optional[str]:
    if key not in d:
        return None
    if not isinstance(d[key], typ):
        return msg
    return None


def require_number_range(d: Dict[str, Any], key: str, lo: float, hi: float) -> Optional[str]:
    if key not in d:
        return None
    v = d[key]
    if not is_number(v):
        return f"'{key}' debe ser numérico."
    if not (lo <= float(v) <= hi):
        return f"'{key}' fuera de rango [{lo}, {hi}]. Valor={v}"
    return None


def require_list_len(d: Dict[str, Any], key: str, n: int) -> Optional[str]:
    if key not in d:
        return None
    v = d[key]
    if not isinstance(v, (list, tuple)) or len(v) != n:
        return f"'{key}' debe ser lista/tupla de longitud {n}."
    return None


def safe_json_dump(obj: Any, path: Path):
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
