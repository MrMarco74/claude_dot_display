"""MIT-licensed iDotMatrix BLE driver.

Derived clean-room from observed wire traffic; see PROTOCOL.md.
"""

from dotdisplay.ble import protocol
from dotdisplay.ble.client import PanelClient
from dotdisplay.ble.transport import BleakTransport, FakeTransport, NotConnected

__all__ = ["protocol", "PanelClient", "BleakTransport", "FakeTransport",
           "NotConnected"]
