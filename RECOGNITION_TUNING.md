# Face Recognition Tuning Guide

This document explains how to manually adjust the sensitivity and image quality of the PPE Detection System in `dashboard/views.py`.

## 1. Recognition Sensitivity (Threshold)
The **Distance (Dist)** score measures how different a face is from the database. 
- **Lower Number** = Strict (Tighten)
- **Higher Number** = Relaxed (Loosen)

### Where to Edit:
Look for the `face_recognition_worker` function around **Line 118**:

```python
# Threshold for ArcFace (0.55 for strict CCTV matching)
if best_dist < 0.55:  # <--- ADJUST THIS NUMBER
    emp = Employee.objects.filter(photo__icontains=best_id).first()
```

### Tuning Impact:
| Threshold | Result | Best For |
| :--- | :--- | :--- |
| **0.40** | **Extreme Strictness** | Preventing false matches at any cost. |
| **0.55** | **Recommended** | High-quality CCTV (Main Stream 101). |
| **0.65** | **Relaxed** | Low-light or blurry sub-streams. |
| **0.80+** | **Unstable** | Many false positives (it will guess wrong). |

---

## 2. Image Enlargement (Zoom)
If faces are far away and small, the AI needs more pixels. We use "Dynamic Scaling."

### Where to Edit:
Look for the scaling logic around **Line 83**:

```python
# Scale more if the face is smaller
orig_h = face_crop.shape[0]
scale = 1.0
if orig_h < 100: scale = 4.0   # Very far: 400% zoom
elif orig_h < 200: scale = 3.0 # Medium distance: 300% zoom
elif orig_h < 400: scale = 2.0 # Near: 200% zoom
```

### Tuning Impact:
- **Increase Scale (e.g., 4.0 -> 6.0):** Helps the AI see details on faces that are very far away. 
- **Warning:** Scaling too much (above 6x) can make the image look like "Lego blocks" (pixelated) and reduce accuracy.

---

## 3. Head Area Cropping
By default, the system focuses on the top 35% of a detected person to find the face.

### Where to Edit:
Look for `head_h` in the `_process_face_mode` function around **Line 282**:

```python
# Top 35% of the person box is usually where the head is
head_h = int(bh * 0.35) # <--- ADJUST THIS RATIO
```

### Tuning Impact:
- **Decrease (0.25):** Focuses strictly on the top of the head/forehead (good if people wear hats).
- **Increase (0.45):** Includes more of the shoulders and neck.

---

## Summary of Goals
- **If getting "Unknown" for yourself:** Increase the Threshold (e.g., 0.55 -> 0.60).
- **If getting "Wrong Person":** Decrease the Threshold (e.g., 0.55 -> 0.45).
- **If detection fails on far people:** Increase the `scale` for small `orig_h`.
