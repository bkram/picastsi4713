# Audio Effects Chain Setup Guide

## Overmodulation Fix

**Problem**: Input levels peaking at -1 to -3 dBFS causing `OVERMOD!!!` warnings.

**Solution**: Reduce mpv output volume in `cfg/config.yaml`:

```yaml
audio_player_cmd: mpv --volume=50  # 50% volume to prevent overmod
```

**Result**: ASQ levels now stable at -21 to -27 dBFS with no overmod warnings.

**Note**: You may need to adjust the volume value (30-70) depending on your audio source level.

---

## Overview

This document describes how to configure audio effects (compressor/limiter) for the PiCast SI4713 FM transmitter project.

## Current Status

**LADSPA ALSA plugin is NOT available** in standard Debian/Raspberry Pi OS repositories. The `libasound2-plugins` package does not include the LADSPA PCM module (`libasound_module_pcm_ladspa.so`).

### What Works
- ✅ Direct ALSA output via `softvol` → `dmix` → hardware
- ✅ Volume control via software mixer
- ✅ Hardware supports 48kHz/16-bit stereo

### What Doesn't Work (Out of the Box)
- ❌ LADSPA plugins directly in ALSA (`pcm.ladspa` type fails with "Slave PCM not usable")
- ❌ Compressor/limiter chain at the ALSA level without additional setup

## Alternative Solutions

### Option 1: Use PipeWire with LADSPA (Recommended)

If your system uses PipeWire instead of pure ALSA:

```bash
# Install PipeWire with LADSPA support
sudo apt-get install pipewire pipewire-audio-client-libspa-ladspa ladspa-plugin-dylap

# Configure PipeWire to load LADSPA filters
# See: https://docs.pipewire.org/page_media_io.html
```

### Option 2: Software-Based Compression in Application

Apply compression/limiting in the Python application before sending audio to ALSA:

```python
import numpy as np

class Limiter:
    """Simple sample-by-sample limiter."""
    def __init__(self, threshold_db=-1.0, release_ms=10):
        self.threshold = 10**(threshold_db / 20.0)
        self.release = release_ms / 1000.0
        self.attack = 0.001
        self.gain = 1.0
        
    def process(self, samples):
        """Process a chunk of audio samples (float32, -1.0 to 1.0)."""
        output = []
        for sample in samples:
            level = abs(sample)
            if level > self.threshold:
                # Attack
                self.gain *= self.attack / (self.attack + 1.0)
            else:
                # Release
                self.gain += (1.0 - self.gain) * self.release / (self.release + 1.0)
            output.append(sample * min(1.0, self.gain))
        return np.array(output, dtype=np.float32)
```

### Option 3: Build ALSA LADSPA Plugin from Source

```bash
# Clone and build alsa-plugins with LADSPA support
git clone https://github.com/alsa-project/alsa-plugins.git
cd alsa-plugins
./autogen.sh --with-ladspa-dir=/usr/lib/ladspa
make
sudo make install

# Verify installation
ls /usr/lib/aarch64-linux-gnu/alsa-lib/libasound_module_pcm_ladspa.so
```

⚠️ **Warning**: Building from source may require matching library versions and can break system audio.

### Option 4: Use JACK Audio Server

```bash
# Install JACK and LADSPA tools
sudo apt-get install jackd2 zita-convolver swh-plugins

# Start JACK with LADSPA
jackd -d alsa -d hw:sndrpihifiberry &
jack_lsp | grep sc4  # List available LADSPA plugins

# Connect audio with ladspa-cmdline or qjackctl
```

## Working ALSA Configuration

The following `/etc/asound.conf provides volume control without effects:

```conf
# /etc/asound.conf
pcm.dmixed {
    type dmix
    ipc_key 1024
    slave {
        pcm "hw:sndrpihifiberry,0"
        rate 48000
        channels 2
        period_time 0
        period_size 1024
        buffer_size 8192
    }
}

pcm.softvol {
    type softvol
    slave.pcm "dmixed"
    control { name "PCM" index 0 }
    min_dB -90.0
    max_dB 0.0
    resolution 256
}

pcm.!default {
    type plug
    slave.pcm "softvol"
}

ctl.!default {
    type hw
    card sndrpihifiberry
}
```

### Testing the Config

```bash
# Test audio playback
speaker-test -D default -c2 -t sine -f 1000 -l 1

# Check volume control
amixer -c sndrpihifiberry sset PCM 50%
```

## LADSPA Plugin Labels (For Reference)

If you successfully enable LADSPA support, these are the correct labels and parameters:

### SC4 Compressor (sc4_1882.so)
```
label: sc4
ports:
  - "Attack time (ms)" 5.0       # Range: 1-20
  - "Release time (ms)" 100.0    # Range: 50-300
  - "Threshold level (dB)" -20.0 # Range: -12 to -30
  - "Ratio (1:n)" 4.0            # Range: 3-10
  - "Knee radius (dB)" 10.0      # Range: 5-15
  - "Makeup gain (dB)" 8.0       # Range: 5-20
```

### Fast Lookahead Limiter (fast_lookahead_limiter_1913.so)
```
label: fastLookaheadLimiter
ports:
  - "Input gain (dB)" 0.0        # Extra boost
  - "Limit (dB)" -1.0            # Ceiling: -0.1 to -3
  - "Release time (s)" 0.2       # Range: 0.05-0.5
```

## Troubleshooting

### "Slave PCM not usable" Error
This means the LADSPA module isn't loaded. Check:
```bash
ls /usr/lib/*/alsa-lib/libasound_module_pcm_ladspa.so
```

If missing, install from source or use an alternative solution above.

### "Device or resource busy"
Another process is using the audio device. Kill conflicting processes:
```bash
pkill -f picast4713.py
fuser -v /dev/snd/*
```

### Overmodulation Warnings
If you see `OVERMOD!!!` in logs, reduce input gain or add software limiting:
- Lower microphone gain
- Implement software compressor (Option 2)
- Adjust makeup gain in SC4 settings

## Related Files

- `/etc/asound.conf` - ALSA configuration
- `/usr/lib/ladspa/*.so` - LADSPA plugin binaries
- `picast4713.py` - Main transmitter application
- `si4713/__init__.py` - Driver with ASQ level monitoring

## References

- [ALSA LADSPA Plugin](https://www.alsa-project.org/main/index.php/LADSPA_plugin) - Not in Debian repos
- [PipeWire LADSPA](https://docs.pipewire.org/page_module_ladspa.html)
- [LADSPA Plugin Collection](http://www.ladspa.org/)
- [CMT Plugins](https://debian.pages.debian.net/cmt/) - Classic LADSPA plugins