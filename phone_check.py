import cv2
import os
import numpy as np
from ultralytics import YOLO

# Optimization for Xeon CPU
os.environ["OPENCV_FFMPEG_THREADS"] = "1"
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|stimeout;5000000"

def run_phone_test():
    model_path = 'models/yolo11s.pt'
    if not os.path.exists(model_path):
        print(f"Error: {model_path} not found.")
        return

    print(f"Loading model: {model_path}...")
    model = YOLO(model_path)

    rtsp_url = "rtsp://admin:xdr54321@192.168.31.104:554/Streaming/Channels/102"
    
    print(f"Connecting to IP Camera: {rtsp_url}")
    cap = cv2.VideoCapture(rtsp_url)

    if not cap.isOpened():
        print("Error: Could not open video stream.")
        return

    print("--- Starting DIGITAL ZOOM (Two-Pass) Phone Detection ---")
    print("Strategy: Find Person -> Crop Area -> Detect Phone inside Crop")

    while True:
        success, frame = cap.read()
        if not success:
            break

        h, w = frame.shape[:2]

        # PASS 1: Detect Persons in full frame
        # Lowering to 0.20 to ensure we never lose the person at extreme side angles
        person_results = model.predict(frame, conf=0.20, classes=[0], verbose=False, imgsz=640)

        for r in person_results:
            for box in r.boxes:
                px1, py1, px2, py2 = map(int, box.xyxy[0])
                p_conf = float(box.conf[0])

                # DIGITAL ZOOM: Define interest area
                # Expanded to 50px buffer to ensure hand/phone is NEVER clipped
                cx1 = max(0, px1 - 50)
                cy1 = max(0, py1 - 50)
                cx2 = min(w, px2 + 50)
                cy2 = min(h, py2 + 50)

                person_crop = frame[cy1:cy2, cx1:cx2]
                
                # Check if crop is valid
                if person_crop.size == 0: continue

                # PASS 2: Detect Phone in the high-detail crop
                # CRITICAL SENSITIVITY: 0.02 threshold. It will catch any pixel resembling a phone.
                phone_results = model.predict(person_crop, conf=0.02, classes=[67], verbose=False, imgsz=640)
                
                is_using_phone = False
                best_phone_conf = 0
                
                for pr in phone_results:
                    if len(pr.boxes) > 0:
                        is_using_phone = True
                        best_phone_conf = float(pr.boxes[0].conf[0])
                        
                        # Draw phone box inside the crop coordinates back to main frame
                        for pbox in pr.boxes:
                            phx1, phy1, phx2, phy2 = map(int, pbox.xyxy[0])
                            cv2.rectangle(frame, (cx1 + phx1, cy1 + phy1), (cx1 + phx2, cy1 + phy2), (0, 0, 255), 2)

                # Visuals
                color = (0, 0, 255) if is_using_phone else (0, 255, 0)
                thickness = 3 if is_using_phone else 2
                cv2.rectangle(frame, (px1, py1), (px2, py2), color, thickness)
                
                label = f"HUMAN: {p_conf:.2f}"
                if is_using_phone:
                    label += f" !! PHONE: {best_phone_conf:.2f} !!"
                
                cv2.putText(frame, label, (px1, py1 - 15), 
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)

        # Show Output
        cv2.imshow("Digital Zoom Phone Test", frame)

        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    run_phone_test()
