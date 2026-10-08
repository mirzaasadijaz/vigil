# Optimization Recommendations for Xeon CPU-Only Server

Current State: 10-Core Xeon / No GPU / 90% CPU Usage.
Goal: Lower CPU overhead for POC delivery while maintaining detection accuracy.

## 1. Violation-Triggered Identification (Implemented)
**Strategy:** Stop running DeepFace on every person in every frame. 
**Impact:** DeepFace (RetinaFace + ArcFace) is the heaviest part of the pipeline. By only identifying individuals when a violation (PPE or Phone) is detected, we reduce identification calls by 80-90% in typical environments.

## 2. OpenVINO Inference (High Priority)
**Strategy:** Export `.pt` models to OpenVINO format.
**Impact:** Xeon processors support AVX-512 instructions. OpenVINO is Intel's native toolkit that allows YOLO to run 2-3x faster on CPUs compared to standard PyTorch.

## 3. Sub-Stream Optimization
**Strategy:** Set IP cameras to a "Sub-Stream" (640x480 @ 10FPS).
**Impact:** Reduces the CPU cycles spent on decoding high-resolution H.264/H.265 video and resizing frames in memory.

## 4. top-Half Phone Detection
**Strategy:** Crop the "Person" bounding box to the top 50% before running the phone detection model.
**Impact:** Reduces the input size for the second YOLO model, speeding up inference and reducing the likelihood of false positives from objects held at waist level.

## 5. Motion-Gated AI
**Strategy:** Use `cv2.absdiff` to check for movement before running YOLO.
**Impact:** If the scene is static (no one is moving), the AI can sleep. This is extremely effective for industrial or office settings during quiet periods.
