# Nachim

Send and control public, finished YouTube videos (H.264/AAC) from macOS 13+ to Android 7+, with ad-free playback and optional subtitles.

## Build and open

Install Homebrew, Xcode Command Line Tools, JDK 17, and Android SDK Command-line Tools. From the project directory:

```sh
brew install yt-dlp ffmpeg deno
brew install --cask android-platform-tools
export ANDROID_HOME="${ANDROID_HOME:-$HOME/Library/Android/sdk}"
"$ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager" 'platforms;android-35' 'build-tools;35.0.0'
./build.sh
open 'build/Nachim.app'
```

## Use

1. Enable **Developer options → USB debugging** on the phone. Unlock it, plug it into the Mac, and accept the debugging prompt.
2. Paste a YouTube link, choose captions if wanted, and click **Send**. The Mac installs the bundled player as needed. Keep the phone unlocked until playback starts.
3. During playback, use the Mac timeline to seek or pause. To change caption language or size, click **Apply to current video**.

The phone deletes a video and its captions when playback finishes or after the first frame of its replacement appears.

**Hotspot:** With USB connected, join the phone’s password-protected hotspot from the Mac. Network debugging on port 5555 is unencrypted. Click **Set up hotspot** under **Phone hotspot** and wait for **Hotspot verified** before unplugging. USB takes priority when reconnected. Repeat setup after a phone reboot. Click **Turn Off** before switching phones.
