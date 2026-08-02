#!/usr/bin/env bash
# Paired GPU power/thermal comparison of auto vs pinned clocks under live
# Frigate. runtime_status is read first each sample because it does not resume
# a suspended device; everything after it does, so the poll itself is part of
# the measurement and the interval is kept long to limit that.
D=/sys/class/drm/card1/device
H=$D/hwmon/hwmon5
PERF=$D/power_dpm_force_performance_level
SAMPLES=${SAMPLES:-30}
INTERVAL=${INTERVAL:-10}

sample_set() {
  local label="$1"
  for i in $(seq 1 "$SAMPLES"); do
    rt=$(cat $D/power/runtime_status 2>/dev/null)
    s=$(grep -o '[0-9]*Mhz \*' $D/pp_dpm_sclk 2>/dev/null | tr -d 'Mhz *')
    m=$(grep -o '[0-9]*Mhz \*' $D/pp_dpm_mclk 2>/dev/null | tr -d 'Mhz *')
    e=$(cat $H/temp1_input 2>/dev/null); j=$(cat $H/temp2_input 2>/dev/null)
    mt=$(cat $H/temp3_input 2>/dev/null)
    p=$(cat $H/power1_input 2>/dev/null); f=$(cat $H/fan1_input 2>/dev/null)
    pw=$(cat $H/pwm1 2>/dev/null); b=$(cat $D/gpu_busy_percent 2>/dev/null)
    inf=$(curl -s localhost:5000/api/stats 2>/dev/null | python3 /home/cvandesande/frigstats.py)
    echo "$label $rt ${s:-NA} ${m:-NA} $(( ${e:-0}/1000 )) $(( ${j:-0}/1000 )) $(( ${mt:-0}/1000 )) $(awk "BEGIN{printf \"%.1f\", ${p:-0}/1000000}") ${f:-NA} ${pw:-NA} ${b:-NA} $inf"
    sleep "$INTERVAL"
  done
}

echo "cond runtime sclk mclk edgeC junctionC memC powerW fanRPM pwm busy inference detfps skipped"
sudo sh -c "echo auto > $PERF"; sleep 5
sample_set AUTO
sudo sh -c "echo high > $PERF"; sleep 5
sample_set HIGH
sudo sh -c "echo auto > $PERF"
echo "RESTORED auto"
