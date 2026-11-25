# One-shot HDR playback probe

This calls the installed Windows plugin worker with Auto HDR forced on, bypassing Jellyfin's client capability gate. It creates a 32-second 4K HEVC HDR10 candidate with AAC audio and serves it over HTTP. Open the printed HLS URL in a compatible player. An optional SceneGraph developer channel is included for clients that support it. The test uses 4K processing and omits frame interpolation.

Run after other GPU playback stops, from the repository root:

~~~powershell
python tests/manual/hdr_probe/test_hdr_probe.py --host 192.168.1.10 --source "D:\path\to\an SDR episode.mkv"
~~~

Use an SDR source at 1080p or below with audio. Replace `--host` with the server's LAN address. The script reads the installed runtime paths, verifies the encoded stream, and prints the generated ZIP and playback URL. The default port is 18194; the player must be able to reach it.

For the optional developer channel, sideload the ZIP with a compatible developer-channel tool and launch it. This occupies the device's developer-channel slot. Leave the terminal serving the clip open.

Watch the channel overlay, the TV's HDR picture mode, and console lines beginning HDRTEST:

- The display event's hdr10=true confirms display capability only.
- The playing_segment event's hdr="hdr10" reports the playing stream as HDR10. The downloaded_segment event's hdrmode=2 is the corresponding HDR10 value. Some firmware versions report unknown or omit these values even when the display enters HDR mode.
- Picture and sound should play normally, and the TV should enter its HDR picture mode. Confirm all three before calling this a pass.

If the clip plays as HDR here, investigate the plugin/client capability negotiation next. If encoding passes but the player reports none or rejects playback, investigate HDR signaling and packaging next. Ten-bit output or an HDR-capable display alone is not a pass.

Exit playback and press Ctrl+C to stop the local server. Generated files and encoding.json stay under dist/hdr-test/.

Draft checks without GPU use or device changes:

~~~powershell
python -m unittest discover -s tests/manual/hdr_probe -p test_probe.py
python tests/manual/hdr_probe/test_hdr_probe.py --host 192.168.1.10 --package-only
~~~

[Auto HDR behavior](../../../docs/enhancements.md#auto-hdr)
