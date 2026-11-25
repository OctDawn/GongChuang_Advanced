import os
import sys

from ultralytics import YOLO

DATA = '/home/lenovo/datasets/shape18_real2/data.yaml'
print('data exists:', os.path.exists(DATA), flush=True)

model = YOLO('models_try/shape_best_3.pt')
print('model loaded', flush=True)
model.train(
    data=DATA,
    epochs=int(sys.argv[1]) if len(sys.argv) > 1 else 30,
    imgsz=640, batch=8, freeze=10, lr0=0.003, patience=15,
    project='runs/night3', name='R1', exist_ok=True,
    device=0, workers=8, cache=False, plots=False,
)
