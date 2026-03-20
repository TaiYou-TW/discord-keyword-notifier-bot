from enum import Enum


class HolodexNotifyType(str, Enum):
    LIVE = "live"
    UPCOMING = "upcoming"
    UPLOAD = "upload"
