from backend.services.analytics.base import Analytic


class FireSmoke(Analytic):
    module_id = "fire_smoke"

    def __init__(self, cam_id, config):
        super().__init__(cam_id, config)

    def process(self, tracks, faces, shape):
        return [], []

    def process_detections(self, detections, shape):
        """Process fire/smoke detections and return (events, overlays).
        detections: list of dicts from fire_smoke detector
        """
        h, w = shape
        events = []
        overlays = []

        for d in detections:
            cls_name = d.get("class_name", "unknown")
            conf = d.get("conf", 0)
            x1, y1, x2, y2 = d.get("box", (0, 0, 0, 0))

            event_type = cls_name
            if self._cooldown(f"{cls_name}_{self.cam_id}", 30.0):
                events.append({
                    "event_type": event_type,
                    "value": round(conf, 3),
                    "data": {"confidence": conf, "class_id": d.get("class_id")},
                    "snapshot": True,
                })

            overlays.append({
                "kind": "box",
                "box": [x1 / w, y1 / h, x2 / w, y2 / h],
                "color": "dnd",
                "label": f"{cls_name.upper()} {conf:.2f}",
                "snapshot": True,
            })

        if detections and self._cooldown("alert_banner", 10.0):
            unique = set(d.get("class_name", "unknown") for d in detections)
            text = " + ".join(sorted(unique))
            overlays.append({
                "kind": "label",
                "text": f"ALERT: {text.upper()} DETECTED!",
                "color": "dnd",
                "snapshot": True,
            })

        return events, overlays
