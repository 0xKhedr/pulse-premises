
def check_thresholds(node_type: str, data: dict, capabilities: list[str] | None = None):

    alarms = []

    def to_float(value, default=0):
        try:
            if value is None:
                return default
            return float(value)
        except Exception:
            return default

    def is_true(value):
        return value is True or value in ["true", "True", "1", 1]

    caps = set(capabilities or [])

    # fallback for old devices without saved capabilities
    if not caps:
        if node_type in ["environment", "energy", "safety", "occupancy"]:
            caps.add(node_type)
        elif node_type == "multi":
            caps.update(["environment", "energy", "safety", "occupancy"])

    if "environment" in caps:
        if to_float(data.get("temperature")) > 30:
            alarms.append("High temperature")
        if to_float(data.get("humidity")) > 80:
            alarms.append("High humidity")

    if "safety" in caps:
        if is_true(data.get("alarm")):
            alarms.append("Fire/Smoke detected")
        if is_true(data.get("smoke")):
            alarms.append("Smoke detected")
        if is_true(data.get("gas")):
            alarms.append("Gas detected")
        if is_true(data.get("water_leak")):
            alarms.append("Water leak detected")

    if "occupancy" in caps:
        if is_true(data.get("motion")):
            alarms.append("Motion detected")
        if is_true(data.get("presence")):
            alarms.append("Presence detected")

    if "energy" in caps:
        if to_float(data.get("power")) > 5000:
            alarms.append("High power usage")
        if to_float(data.get("voltage")) > 260:
            alarms.append("High voltage")
        if to_float(data.get("current")) > 20:
            alarms.append("High current")

    return alarms


def enrich_battery_telemetry(telemetry: dict):

    telemetry = telemetry.copy()
    power_source = telemetry.get("power_source")

    if power_source in ["usb", "mains", "external"]:
        telemetry["battery_status"] = "usb_powered"
        return telemetry

    battery_percent = telemetry.get("battery_percent")
    battery_voltage = telemetry.get("battery_voltage")

    # If voltage exists but percent does not, estimate percent for a 1-cell Li-ion battery.
    # 4.2V = 100%, 3.3V = 0%
    if battery_percent is None and battery_voltage is not None:
        try:
            voltage = float(battery_voltage)
            percent = ((voltage - 3.3) / (4.2 - 3.3)) * 100
            percent = max(0, min(100, percent))
            telemetry["battery_percent"] = round(percent, 1)
            battery_percent = telemetry["battery_percent"]
        except Exception:
            pass

    if battery_percent is not None:
        try:
            percent = float(battery_percent)

            if percent <= 10:
                telemetry["battery_status"] = "critical"
            elif percent <= 20:
                telemetry["battery_status"] = "low"
            else:
                telemetry["battery_status"] = "ok"

        except Exception:
            telemetry["battery_status"] = "unknown"

    elif battery_voltage is not None:
        telemetry["battery_status"] = "voltage_only"

    else:
        telemetry["battery_status"] = "not_reported"

    return telemetry



def check_battery_alarms(telemetry: dict):

    alarms = []

    battery_status = telemetry.get("battery_status")

    if battery_status == "critical":
        alarms.append("Critical battery level")
    elif battery_status == "low":
        alarms.append("Low battery level")

    return alarms


def enrich_signal_telemetry(ttn_data: dict, telemetry: dict):

    telemetry = telemetry.copy()

    uplink = ttn_data.get("uplink_message", {})
    rx_metadata = uplink.get("rx_metadata", []) or []
    settings = uplink.get("settings", {}) or {}

    best_rx = None

    if rx_metadata:
        # Choose the gateway reception with the strongest RSSI
        best_rx = max(
            rx_metadata,
            key=lambda rx: rx.get("rssi", -999)
        )

        telemetry["received_by_gateways"] = len(rx_metadata)

        gateway_ids = best_rx.get("gateway_ids", {}) or {}

        telemetry["gateway_id"] = gateway_ids.get("gateway_id")
        telemetry["rssi"] = best_rx.get("rssi")
        telemetry["snr"] = best_rx.get("snr")

    lora_settings = (
        settings
        .get("data_rate", {})
        .get("lora", {})
    )

    if lora_settings:
        telemetry["spreading_factor"] = lora_settings.get("spreading_factor")
        telemetry["bandwidth"] = lora_settings.get("bandwidth")

    if uplink.get("frequency"):
        telemetry["frequency"] = uplink.get("frequency")

    rssi = telemetry.get("rssi")
    snr = telemetry.get("snr")

    if rssi is None and snr is None:
        telemetry["signal_status"] = "not_reported"
        return telemetry

    try:
        rssi_value = float(rssi) if rssi is not None else None
        snr_value = float(snr) if snr is not None else None

        if (
            (rssi_value is not None and rssi_value <= -120)
            or
            (snr_value is not None and snr_value <= -10)
        ):
            telemetry["signal_status"] = "poor"

        elif (
            (rssi_value is not None and rssi_value <= -110)
            or
            (snr_value is not None and snr_value <= -5)
        ):
            telemetry["signal_status"] = "weak"

        else:
            telemetry["signal_status"] = "good"

    except Exception:
        telemetry["signal_status"] = "unknown"

    return telemetry



def check_signal_alarms(telemetry: dict):

    alarms = []

    signal_status = telemetry.get("signal_status")

    if signal_status == "poor":
        alarms.append("Poor LoRa signal quality")

    return alarms








