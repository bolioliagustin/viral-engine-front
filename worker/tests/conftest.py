"""
Fixtures compartidas del worker.

`main.py` llama a `setup_logging()` al importarse, que reemplaza `builtins.print`
por un wrapper que manda todo al logger. En la suite eso depende del ORDEN de
los tests: cualquier test que importe `main` deja a los siguientes sin stdout
capturable (capsys). Este autouse restaura el `print` original después de cada
test, así importar `main` no contamina a los demás archivos.
"""
import builtins

import pytest

_ORIGINAL_PRINT = builtins.print


@pytest.fixture(autouse=True)
def _restore_builtin_print():
    yield
    builtins.print = _ORIGINAL_PRINT
