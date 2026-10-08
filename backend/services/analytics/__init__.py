"""Rules-engine factory. Maps a module id to its stateful consumer class.

Attendance is handled specially in the perception worker (face match in child,
DB writes in the main process), so it is NOT a standard consumer here. These are
the person-track rule consumers (Phase 1 + Intrusion in Phase 2) plus mobile.
"""
from backend.services.analytics.headcount import HeadCount
from backend.services.analytics.occupancy import Occupancy
from backend.services.analytics.idle import IdleTime
from backend.services.analytics.intrusion import Intrusion
from backend.services.analytics.mobile import MobileUsage
from backend.services.analytics.fire_smoke import FireSmoke
from backend.services.analytics.truck_count import TruckCount
from backend.services.analytics.tripwire import TripWire
from backend.services.analytics.vehicle_count import VehicleCount
from backend.services.analytics.crowd import Crowd
from backend.services.analytics.no_stopping import NoStopping

_CONSUMERS = {
    "vehicle_count": VehicleCount,
    "headcount": HeadCount,
    "truck_count": TruckCount,
    "occupancy": Occupancy,
    "idle": IdleTime,
    "intrusion": Intrusion,
    "mobile": MobileUsage,
    "fire_smoke": FireSmoke,
    # Kohat POC. `no_stopping` is driven by process_detections (like fire_smoke), so its
    # process() is inert; it is listed here so make_consumer can build it.
    "tripwire": TripWire,
    "crowd": Crowd,
    "no_stopping": NoStopping,
}


def make_consumer(module_id: str, cam_id, config: dict):
    cls = _CONSUMERS.get(module_id)
    return cls(cam_id, config) if cls else None


def is_person_analytic(module_id: str) -> bool:
    return module_id in _CONSUMERS
