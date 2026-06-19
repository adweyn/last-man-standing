import math
import array
import pygame

# Synthesized sounds dictionary
_sounds = {}

def init_synth_sounds():
    """Pre-generates retro synth sound effects on startup to avoid latency."""
    if not pygame.mixer or not pygame.mixer.get_init():
        try:
            pygame.mixer.init(frequency=22050, size=-16, channels=2)
        except Exception:
            return  # Audio mixer not available on this system

    sample_rate = 22050

    def make_sound(samples, vol=1.0):
        # Create stereo 16-bit sound buffer
        buf = array.array('h')
        for s in samples:
            val = int(s * vol * 32767)
            val = max(-32768, min(32767, val))
            # Duplicate mono for stereo channels
            buf.append(val)
            buf.append(val)
        return pygame.mixer.Sound(buffer=buf)

    # 1. Shoot / Attack (descending chirp sweep)
    try:
        dur = 0.12
        n = int(sample_rate * dur)
        s_shoot = []
        for i in range(n):
            t = i / sample_rate
            freq = 700 - (700 - 180) * (t / dur)
            val = math.sin(2 * math.pi * freq * t)
            env = 1.0 - (t / dur)
            s_shoot.append(val * env)
        _sounds["shoot"] = make_sound(s_shoot, 0.18)
    except Exception:
        pass

    # 2. Pickup / Collect Crystal (high arpeggio chimes)
    try:
        dur = 0.22
        n = int(sample_rate * dur)
        s_pickup = []
        for i in range(n):
            t = i / sample_rate
            freq = 988 if t < 0.08 else 1318
            val = math.sin(2 * math.pi * freq * t)
            env = 1.0 - (t / dur)
            s_pickup.append(val * env)
        _sounds["pickup"] = make_sound(s_pickup, 0.12)
    except Exception:
        pass

    # 3. Hit / Damage Taken (rough noise thud)
    try:
        dur = 0.15
        n = int(sample_rate * dur)
        s_hit = []
        import random
        for i in range(n):
            t = i / sample_rate
            val = 0.6 * random.uniform(-1, 1) + 0.4 * (1.0 if math.sin(2 * math.pi * 110 * t) >= 0 else -1.0)
            env = 1.0 - (t / dur)
            s_hit.append(val * env)
        _sounds["hit"] = make_sound(s_hit, 0.22)
    except Exception:
        pass

    # 4. Death (dramatic descending slide)
    try:
        dur = 0.6
        n = int(sample_rate * dur)
        s_death = []
        for i in range(n):
            t = i / sample_rate
            freq = 320 - (320 - 55) * (t / dur)
            val = math.sin(2 * math.pi * freq * t)
            env = 1.0 - (t / dur)
            s_death.append(val * env)
        _sounds["death"] = make_sound(s_death, 0.35)
    except Exception:
        pass

    # 5. Alert / Boss Warning (pulsing alarm siren)
    try:
        dur = 0.65
        n = int(sample_rate * dur)
        s_alert = []
        for i in range(n):
            t = i / sample_rate
            freq = 550 + 120 * math.sin(2 * math.pi * 5 * t)
            val = 1.0 if math.sin(2 * math.pi * freq * t) >= 0 else -1.0
            env = 1.0
            if i > n - 1200:
                env = (n - i) / 1200
            s_alert.append(val * env)
        _sounds["alert"] = make_sound(s_alert, 0.08)
    except Exception:
        pass

    # 6. Victory (triumphant arpeggio)
    try:
        dur = 0.8
        n = int(sample_rate * dur)
        s_victory = []
        notes = [523, 659, 784, 1046]
        for i in range(n):
            t = i / sample_rate
            note_idx = min(3, int(t / 0.15))
            freq = notes[note_idx]
            val = math.sin(2 * math.pi * freq * t)
            env = 1.0 - (t / dur)
            s_victory.append(val * env)
        _sounds["victory"] = make_sound(s_victory, 0.25)
    except Exception:
        pass


def play_sound(name: str):
    """Plays a pre-generated synth sound effect."""
    sound = _sounds.get(name)
    if sound:
        try:
            sound.play()
        except Exception:
            pass
