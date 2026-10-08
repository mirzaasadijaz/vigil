import cv2
import sys
import time
import os

# Force TCP and set a strict 5-second timeout to prevent hanging or multiple retries
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|stimeout;5000000"

def test_rtsp(rtsp_url):
    print(f"\n[INFO] Testing RTSP connection to:\n       {rtsp_url}\n")
    print("[INFO] Attempting to connect (timeout = 5s)...")
    
    start_time = time.time()
    
    # Try to open the stream. We use CAP_FFMPEG to ensure it doesn't fall back to CAP_IMAGES
    cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
    
    if not cap.isOpened():
        print("\n❌ [ERROR] Failed to open the RTSP stream.")
        print("    -> The NVR rejected the connection or is unreachable.")
        print("    -> Double-check your IP, port, username, password, and channel suffix.")
        print("    -> Make sure your NVR account is unlocked and not blacklisting your server's IP.")
        sys.exit(1)
        
    print(f"✅ [SUCCESS] Connection established! (Took {time.time() - start_time:.2f}s)")
    print("[INFO] Attempting to grab a single frame...")
    
    ret, frame = cap.read()
    
    if not ret or frame is None:
        print("\n❌ [ERROR] Connected to stream, but failed to grab a frame.")
        print("    -> This can happen if the channel doesn't exist, is disabled, or the codec is unsupported.")
        cap.release()
        sys.exit(1)
        
    print(f"✅ [SUCCESS] Frame grabbed successfully! (Resolution: {frame.shape[1]}x{frame.shape[0]})")
    
    output_filename = "test_frame_output.jpg"
    cv2.imwrite(output_filename, frame)
    print(f"✅ [SUCCESS] Saved test frame as '{output_filename}' in the current directory.")
    
    cap.release()
    print("\nTest completed successfully. It is safe to use this URL in the dashboard.\n")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python test_rtsp.py <rtsp_url>")
        sys.exit(1)
        
    test_rtsp(sys.argv[1])
