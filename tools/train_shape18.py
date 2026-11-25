import os
import sys

from ultralytics import YOLO

DATA = sys.argv[2] if len(sys.argv) > 2 else os.path.expanduser('~/datasets/shape18_train/data.yaml')
NAME = sys.argv[3] if len(sys.argv) > 3 else 'R2'
EPOCHS = int(sys.argv[1]) if len(sys.argv) > 1 else 45
INIT = sys.argv[4] if len(sys.argv) > 4 else 'models_try/shape_best_3.pt'
PROJECT = sys.argv[5] if len(sys.argv) > 5 else 'runs/night4'

print('init:', INIT, flush=True)
print('data:', DATA, os.path.exists(DATA), flush=True)
print('epochs:', EPOCHS, 'name:', NAME, flush=True)

model = YOLO(INIT)
model.train(
    data=DATA, epochs=EPOCHS, imgsz=640, batch=8, freeze=10, lr0=0.002, patience=20,
    project=PROJECT, name=NAME, exist_ok=True,
    device=0, workers=8, cache=False, plots=False,
)
