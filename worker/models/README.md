# worker/models/

Modelos binarios versionados que usa el worker (no van en requirements.txt
porque no son paquetes Python).

## face_detection_yunet_2023mar.onnx

- **Qué es:** YuNet, detector de caras liviano (CPU, ~230 KB) usado por
  `services/reframe.py` (W5 — docs/PLAN_CALIDAD.md §9 Fase 1) vía
  `cv2.FaceDetectorYN`.
- **Origen:** [opencv/opencv_zoo](https://github.com/opencv/opencv_zoo),
  `models/face_detection_yunet/face_detection_yunet_2023mar.onnx`. El modelo
  en sí viene originalmente de
  [ShiqiYu/libfacedetection.train](https://github.com/ShiqiYu/libfacedetection.train).
- **Licencia:** Apache License 2.0 (licencia del repo `opencv_zoo`; ver
  `LICENSE` en este mismo directorio). Libre para uso comercial, sin
  restricciones de redistribución.
- **Por qué esta variante y no `face_detection_yunet_2026may.onnx`:** el
  README del modelo en opencv_zoo aclara que `_2023mar` tiene *input shape
  fijo* — pensado para el motor DNN de OpenCV 4.x — mientras que `_2026may`
  usa dims simbólicas para el motor ONNX Runtime nuevo de OpenCV 5.x. En este
  proyecto (`opencv-python-headless` 5.x) `_2023mar` funciona igual —
  `cv2.FaceDetectorYN.create(...)` emite un warning
  (`Targets are not supported by the new graph engine for now`) pero detecta
  caras correctamente (verificado en `tests/test_reframe.py` y en el render
  de validación de W5) — así que se mantuvo por ser la variante más chica y
  la que documentan más ejemplos.
- **Cómo se usa:** `services/reframe.py::_get_face_detector` lo carga por
  path fijo (`MODEL_PATH` en ese módulo) bajo demanda, sin descargarlo en
  runtime — el .onnx queda versionado en git como cualquier otro asset del
  repo (232 KB, no es un problema de tamaño).
