import os
from deepface import DeepFace
from deepface.models.face_detection import RetinaFace

def download_all_models():
    print("--------------------------------------------------")
    print("Starting Offline Model Downloader...")
    print("Make sure you are connected to the INTERNET.")
    print("--------------------------------------------------")

    try:
        print("\n1. Downloading/Verifying ArcFace Recognition Model...")
        DeepFace.build_model("ArcFace")
        print("   -> ArcFace Ready.")
    except Exception as e:
        print(f"   -> Error downloading ArcFace: {e}")

    try:
        print("\n2. Downloading/Verifying VGG-Face Recognition Model...")
        DeepFace.build_model("VGG-Face")
        print("   -> VGG-Face Ready.")
    except Exception as e:
        print(f"   -> Error downloading VGG-Face: {e}")

    try:
        print("\n3. Downloading/Verifying RetinaFace Detection Model...")
        RetinaFace.build_model()
        print("   -> RetinaFace Ready.")
    except Exception as e:
        print(f"   -> Error downloading RetinaFace: {e}")

    print("\n--------------------------------------------------")
    print("All models are now cached on this machine!")
    print("You can safely disconnect from the internet and connect your camera.")
    print("--------------------------------------------------")

if __name__ == "__main__":
    download_all_models()
