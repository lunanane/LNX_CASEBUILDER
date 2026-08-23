# AMYboard Sketch
# DESCRIPTION: Juno-style synth on Ch 3, Audio-In Ducking on Ch 1, CV/Gate out on Ch 4.
# Press encoder 1 -> mode 0: Patch, Cutoff, Res, Amp
# Press encoder 2 -> mode 1: Effect Macros (Rev, Cho, Echo, EQ)
# Press encoder 3 -> mode 2: Amp ADSR
# Press encoder 4 -> mode 3: Filter ADSR

import amy
import amyboard
import sequencer

sequencer.tempo(120)

# -----------------
# MIDI Channel Allocations (0-indexed)
# -----------------
KICK_CH = 0   # MIDI Ch 1: Triggers the ducking envelope
SYNTH_CH = 2  # MIDI Ch 3: The Juno-6 poly synth
CV_CH = 3     # MIDI Ch 4: Monophonic CV/Gate out

# -----------------
# Synth Parameters (Juno on Ch 3)
# -----------------
current_patch = 0
filter_freq   = 2000.0
resonance     = 1.0
amp_val       = 1.0

# Amp ADSR
amp_a = 50.0
amp_d = 250.0
amp_s = 0.8
amp_r = 400.0

# Filter ADSR
filt_a = 50.0
filt_d = 250.0
filt_s = 0.5
filt_r = 400.0

# -----------------
# Effect Macro Knobs (0.0 to 1.0)
# -----------------
rev_knob  = 0.0
cho_knob  = 0.0
echo_knob = 0.0
eq_knob   = 0.0

rev_liveness  = 0.80
rev_xover     = 4000.0
cho_freq      = 0.5
echo_feedback = 0.30
echo_filter   = 0.0

def _amp_bp_str():
    return "%d,1.0,%d,%.2f,%d,0" % (int(amp_a), int(amp_d), amp_s, int(amp_r))

def _filt_bp_str():
    return "%d,1.0,%d,%.2f,%d,0" % (int(filt_a), int(filt_d), filt_s, int(filt_r))

def _rev_str():
    rev_level = min(1.0, rev_knob * 2.0)
    rev_damping = max(0.0, (rev_knob - 0.5) * 2.0)
    return "%.3f,%.3f,%.3f,%.0f" % (rev_level, rev_liveness, rev_damping, rev_xover)

def _cho_str():
    cho_level = min(1.0, cho_knob * 2.0)
    cho_depth = max(0.0, (cho_knob - 0.5) * 2.0)
    return "%.3f,2.0,%.3f,%.3f" % (cho_level, cho_freq, cho_depth)

def _echo_str():
    echo_level = min(1.0, echo_knob * 2.0)
    echo_delay = 200.0 + max(0.0, (echo_knob - 0.5) * 2.0 * 800.0)
    return "%.3f,%.0f,2000,%.3f,%.3f" % (echo_level, echo_delay, echo_feedback, echo_filter)

def _eq_str():
    eq_h = min(1.0, eq_knob * 2.0) * 15.0
    eq_m = max(0.0, (eq_knob - 0.5) * 2.0) * 15.0
    return "0.0,%.2f,%.2f" % (eq_m, eq_h)

# -----------------
# 1. Main Synth Setup (MIDI Ch 3)
# -----------------
amy.send(synth=SYNTH_CH, patch=current_patch, num_voices=6)
amy.send(synth=SYNTH_CH, filter_type=amy.FILTER_LPF24, filter_freq=filter_freq, resonance=resonance, amp=amp_val)
amy.send(synth=SYNTH_CH, bp0=_amp_bp_str(), bp1=_filt_bp_str())

# -----------------
# 2. Sidechain Ducking Setup (MIDI Ch 1)
# -----------------
# Voice 9 acts as an infinite drone passing the incoming audio.
amy.send(v=9, wave=amy.AUDIO_IN, amp=1.0, vel=1, note=60)

# Voice 10 listens to Ch 1 (Kick) and triggers an inverted envelope to modulate Voice 9's amplitude.
amy.send(synth=KICK_CH, wave=amy.SINE, amp=0)
amy.send(synth=KICK_CH, bp0="0,1.0, 20,0.0, 150,1.0, 0,0")

# -----------------
# 3. CV / Gate Output Setup (MIDI Ch 4)
# -----------------
# 1V/Oct pitch tracking for Channel 4 out of CV 1 (Channel 0)
amyboard.set_cv_out(channel=0, synth=CV_CH)

# Custom MIDI callback to fire a 5V Gate out of CV 2 (Channel 1)
def midi_callback(msg):
    status = msg[0]
    channel = status & 0x0F
    msg_type = status & 0xF0
    
    if channel == CV_CH:
        if msg_type == 0x90 and msg[2] > 0:
            amyboard.cv_out(5.0, channel=1)
        elif msg_type == 0x80 or (msg_type == 0x90 and msg[2] == 0):
            amyboard.cv_out(0.0, channel=1)

try:
    amy.midi_cb = midi_callback
except AttributeError:
    pass

# Send initial effect states
amy.send(reverb=_rev_str())
amy.send(chorus=_cho_str())
amy.send(echo=_echo_str())
amy.send(eq=_eq_str())

# -----------------
# Display Setup
# -----------------
try:
    amyboard.init_display()
    _display_ok = True
except Exception:
    _display_ok = False

mode = 0
MODE_NAMES = ["SYNTHESIS", "EFFECTS", "AMP ENVELOPE", "FILT ENVELOPE"]
MODE_COLORS = [
    (0, 80, 80),
    (80, 0, 80),
    (80, 80, 0),
    (0, 80, 0)
]

def draw():
    if not _display_ok:
        return
    d = amyboard.display
    d.fill(0)
    d.text(MODE_NAMES[mode], 0, 0, 255)
    d.hline(0, 10, 128, 180)
    
    if mode == 0:
        d.text("Patch  " + str(current_patch), 0, 14, 255)
        d.text("Cutoff " + str(int(filter_freq)) + "Hz", 0, 26, 255)
        d.text("Res    " + ("%.2f" % resonance), 0, 38, 255)
        d.text("Amp    " + ("%.2f" % amp_val), 0, 50, 255)
    elif mode == 1:
        d.text("Reverb " + ("%d%%" % int(rev_knob * 100)), 0, 14, 255)
        d.text("Chorus " + ("%d%%" % int(cho_knob * 100)), 0, 26, 255)
        d.text("Echo   " + ("%d%%" % int(echo_knob * 100)), 0, 38, 255)
        d.text("EQ     " + ("%d%%" % int(eq_knob * 100)), 0, 50, 255)
    elif mode == 2:
        d.text("Attack " + str(int(amp_a)) + "ms", 0, 14, 255)
        d.text("Decay  " + str(int(amp_d)) + "ms", 0, 26, 255)
        d.text("Sust   " + ("%.2f" % amp_s), 0, 38, 255)
        d.text("Rel    " + str(int(amp_r)) + "ms", 0, 50, 255)
    elif mode == 3:
        d.text("Attack " + str(int(filt_a)) + "ms", 0, 14, 255)
        d.text("Decay  " + str(int(filt_d)) + "ms", 0, 26, 255)
        d.text("Sust   " + ("%.2f" % filt_s), 0, 38, 255)
        d.text("Rel    " + str(int(filt_r)) + "ms", 0, 50, 255)
        
    d.text("Btn1-4: Page", 0, 110, 120)
    d.show()

draw()

enc = amyboard.encoder()
_prev = [enc.read(i) for i in range(enc.encoders)]
_btn_prev = [False] * enc.encoders

def _set_mode_leds():
    for i in range(enc.encoders):
        if i < enc.leds:
            if i == mode:
                r, g, b = MODE_COLORS[mode]
                enc.led(i, r, g, b)
            else:
                enc.led(i, 20, 20, 20)

_set_mode_leds()

# -----------------
# Main Loop
# -----------------
def loop(step):
    global mode, current_patch, filter_freq, resonance, amp_val
    global rev_knob, cho_knob, echo_knob, eq_knob
    global amp_a, amp_d, amp_s, amp_r
    global filt_a, filt_d, filt_s, filt_r
    global _prev, _btn_prev

    changed = False

    for i in range(enc.encoders):
        btn = enc.button(i)
        if btn and not _btn_prev[i]:
            mode = i
            _set_mode_leds()
            changed = True
        _btn_prev[i] = btn

    for i in range(enc.encoders):
        pos = enc.read(i)
        delta = pos - _prev[i]
        _prev[i] = pos
        
        if delta == 0:
            continue
            
        changed = True

        if mode == 0:
            if i == 0:
                current_patch = (current_patch + delta) % 128
                amy.send(synth=SYNTH_CH, patch=current_patch)
            elif i == 1:
                filter_freq = max(50.0, min(8000.0, filter_freq + delta * 50.0))
                amy.send(synth=SYNTH_CH, filter_freq=filter_freq)
            elif i == 2:
                resonance = max(0.0, min(8.0, resonance + delta * 0.1))
                amy.send(synth=SYNTH_CH, resonance=resonance)
            elif i == 3:
                amp_val = max(0.1, min(2.0, amp_val + delta * 0.05))
                amy.send(synth=SYNTH_CH, amp=amp_val)

        elif mode == 1:
            if i == 0:
                rev_knob = max(0.0, min(1.0, rev_knob + delta * 0.02))
                amy.send(reverb=_rev_str())
            elif i == 1:
                cho_knob = max(0.0, min(1.0, cho_knob + delta * 0.02))
                amy.send(chorus=_cho_str())
            elif i == 2:
                echo_knob = max(0.0, min(1.0, echo_knob + delta * 0.02))
                amy.send(echo=_echo_str())
            elif i == 3:
                eq_knob = max(0.0, min(1.0, eq_knob + delta * 0.02))
                amy.send(eq=_eq_str())

        elif mode == 2:
            if i == 0:
                amp_a = max(0.0, min(5000.0, amp_a + delta * 20.0))
            elif i == 1:
                amp_d = max(10.0, min(5000.0, amp_d + delta * 20.0))
            elif i == 2:
                amp_s = max(0.0, min(1.0, amp_s + delta * 0.05))
            elif i == 3:
                amp_r = max(10.0, min(10000.0, amp_r + delta * 20.0))
            amy.send(synth=SYNTH_CH, bp0=_amp_bp_str())

        elif mode == 3:
            if i == 0:
                filt_a = max(0.0, min(5000.0, filt_a + delta * 20.0))
            elif i == 1:
                filt_d = max(10.0, min(5000.0, filt_d + delta * 20.0))
            elif i == 2:
                filt_s = max(0.0, min(1.0, filt_s + delta * 0.05))
            elif i == 3:
                filt_r = max(10.0, min(10000.0, filt_r + delta * 20.0))
            amy.send(synth=SYNTH_CH, bp1=_filt_bp_str())

    if changed:
        draw()