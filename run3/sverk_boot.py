"""Официальный sverk_interfaces.init не принимает drone_id — на плате один дрон."""
from __future__ import annotations


def init_drone(nodename: str):
    import sverk_interfaces
    try:
        return sverk_interfaces.init(Nodename=nodename)
    except TypeError:
        return sverk_interfaces.init(node_name=nodename)
