import cv2
from ultralytics import YOLO

def run_ppe_detection(model_path='ppe.pt', source=0):
    """
    source: can be 0 (webcam) or an RTSP URL for IP cameras.
    Example RTSP with credentials: 'rtsp://admin:password123@192.168.1.100:554/stream'
    """
    # Load the model
    model = YOLO(model_path)
    
    # Open the camera source
    cap = cv2.VideoCapture(source)
    
    if not cap.isOpened():
        print("Error: Could not open camera.")
        return

    print("Starting PPE Detection... Press 'q' to quit.")
    
    frame_count = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break

        frame_count += 1
        # Process only every 10th frame to reduce lag
        if frame_count % 10 == 0:
            # Run YOLO detection on the frame
            results = model.predict(frame, stream=True, conf=0.5, verbose=False)

            # Process results
            for r in results:
                annotated_frame = r.plot() # Draws bounding boxes and labels
                
                # Check for violations
                for box in r.boxes:
                    class_id = int(box.cls[0])
                    label = r.names[class_id].lower()
                    if 'no-' in label or 'not' in label:
                        print(f"WARNING: PPE Violation Detected -> {label}")

                # Show the annotated frame
                cv2.imshow("Real-Time PPE Detection", annotated_frame)
        else:
            # For intermediate frames, just show the raw frame to keep video smooth
            # or skip completely if you want maximum speed. 
            # Showing the raw frame maintains a high FPS feel.
            cv2.imshow("Real-Time PPE Detection", frame)

        # Break loop on 'q' key press
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    # Release resources
    cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    # Updated to channel 102 as requested
    camera_url = 'rtsp://admin:xdr54321@192.168.31.104:554/Streaming/Channels/102'
    
    run_ppe_detection(source=camera_url)
