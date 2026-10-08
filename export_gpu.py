import os
from ultralytics import YOLO

def export_models():
    # 1. PPE Model
    ppe_pt = 'ppe.pt'
    if os.path.exists(ppe_pt):
        print(f"Exporting {ppe_pt} to TensorRT...")
        model = YOLO(ppe_pt)
        # format='engine' is for TensorRT
        # imgsz=640 is standard
        # half=True uses FP16 precision (much faster on GPUs like 1050 Ti)
        model.export(format='engine', device=0, half=True, imgsz=640)
    
    # 2. Person/Phone Model (yolo11s)
    coco_pt = 'models/yolo11s.pt'
    if os.path.exists(coco_pt):
        print(f"Exporting {coco_pt} to TensorRT...")
        model = YOLO(coco_pt)
        model.export(format='engine', device=0, half=True, imgsz=640)

if __name__ == "__main__":
    export_models()
