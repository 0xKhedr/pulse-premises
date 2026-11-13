// Optional TTN JavaScript uplink formatter for this display-only device.
// No sensor telemetry is invented. Nicknames are read from raw FPort 10 by SBMS.
function decodeUplink(input) {
  if (input.fPort === 10) return { data: {} };
  if (input.fPort === 20 && input.bytes.length === 3 && input.bytes[0] === 1) {
    return { data: { epaper_protocol: 1, epaper_has_rendered: input.bytes[1] === 1,
      epaper_body_port: input.bytes[2] } };
  }
  return { errors: ["Unsupported e-paper uplink"] };
}
