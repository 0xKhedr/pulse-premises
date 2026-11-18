# Keep Docker stderr intact and a bounded journal for SBMS. No Docker API access.
# boot is a unique ID for this stack process; sequence survives file rotation.
BEGIN { sequence = 0; size = 0; current = "/var/log/ttn/attempts.current" }
{
    print $0
    fflush()
    # The JSON decoder in SBMS applies the admission-failure and identity filters.
    if ($0 !~ /gatewayserver/ && $0 !~ /\/traffic\// && $0 !~ /\/router-info/) next
    if ($0 !~ /"error"[[:space:]]*:/) next
    if (length($0) > 60000) next
    if (size > 1048576) {
        close(current)
        system("mv -f /var/log/ttn/attempts.current /var/log/ttn/attempts.previous")
        size = 0
    }
    sequence++
    record = boot "\t" sequence "\t" systime() "\t" $0
    print record >> current
    fflush(current)
    size += length(record) + 1
}
