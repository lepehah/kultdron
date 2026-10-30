"""Починка `import cv2` в контейнере sverk_ros2.

Проблема: в образе поверх системного NumPy 1.21 установлен NumPy 2 (в /usr/local),
а системный OpenCV собран под NumPy 1.x. Поэтому `import cv2` падает с ошибкой
«numpy.core.multiarray failed to import» / «_ARRAY_API not found».

Что делает этот файл: только для вашей программы ставит системные пакеты Python
(/usr/lib/python3/dist-packages) перед /usr/local. Тогда numpy и cv2 берутся
системные и совместимые. На дроне ничего не меняется и не удаляется; пакеты из
/usr/local по-прежнему доступны, если их нет среди системных.

Как пользоваться:
  1. Положите numpy_fix.py рядом со своей программой.
  2. Первой строкой программы, ДО import numpy / cv2 / sverk_interfaces:
         import numpy_fix
  3. Проверка: python3 numpy_fix.py — напишет версии numpy и cv2.

Убрать: удалите строку `import numpy_fix` (и файл).
"""
import sys

SYSTEM = "/usr/lib/python3/dist-packages"
LOCAL_PREFIX = "/usr/local/lib/python3"


def reorder(paths: list) -> list:
    """Системные пакеты — сразу перед первым /usr/local; остальной порядок не трогаем."""
    if SYSTEM not in paths:
        return list(paths)
    first_local = next((i for i, p in enumerate(paths) if p.startswith(LOCAL_PREFIX)), None)
    if first_local is None or paths.index(SYSTEM) < first_local:
        return list(paths)
    result = [p for p in paths if p != SYSTEM]
    result.insert(first_local, SYSTEM)
    return result


def apply() -> None:
    numpy = sys.modules.get("numpy")
    if numpy is not None and not numpy.__version__.startswith("1."):
        print("numpy_fix: NumPy %s уже загружен — `import numpy_fix` должен быть первой "
              "строкой программы, до numpy/cv2/sverk_interfaces" % numpy.__version__,
              file=sys.stderr)
        return
    sys.path[:] = reorder(sys.path)


apply()

if __name__ == "__main__":
    import numpy
    print("numpy", numpy.__version__, "из", numpy.__file__)
    try:
        import cv2
        print("cv2", cv2.__version__, "— импортируется, всё в порядке")
    except Exception as e:
        print("cv2 НЕ импортируется:", repr(e))
        sys.exit(1)
