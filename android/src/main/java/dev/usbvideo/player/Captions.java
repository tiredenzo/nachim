package dev.usbvideo.player;

import android.graphics.Color;
import android.os.FileObserver;
import android.text.TextUtils;
import android.view.Gravity;
import android.view.View;
import android.widget.FrameLayout;
import android.widget.TextView;
import android.widget.VideoView;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;

/** Captions follow the VideoView clock, including seeks and pauses. */
final class Captions {
    private final VideoView video;
    private final TextView label;
    private final File directory;
    private final FileObserver changes;
    private JSONArray cues = new JSONArray();
    private String track = "";
    private long[] latestEnds = new long[0];
    private boolean closed;

    Captions(FrameLayout screen, VideoView video, File directory) {
        this.video = video;
        this.directory = directory;
        directory.mkdirs();
        label = new TextView(screen.getContext());
        label.setGravity(Gravity.CENTER);
        label.setTextColor(Color.WHITE);
        label.setBackgroundColor(0xc0000000);
        label.setPadding(12, 2, 12, 2);
        label.setVisibility(View.GONE);
        FrameLayout.LayoutParams box = new FrameLayout.LayoutParams(-2, -2, Gravity.BOTTOM | Gravity.CENTER_HORIZONTAL);
        screen.addView(label, box);
        video.addOnLayoutChangeListener((view, left, top, right, bottom, ol, ot, or, ob) -> {
            label.setMaxWidth((right - left) * 9 / 10);
            box.bottomMargin = screen.getHeight() - screen.getPaddingBottom() - bottom + (bottom - top) / 20;
            label.setLayoutParams(box);
        });
        // The Mac renames settings.json into place after the selected track is ready.
        changes = new FileObserver(directory.getPath(), FileObserver.MOVED_TO) {
            @Override public void onEvent(int event, String name) {
                if ("settings.json".equals(name)) video.post(Captions.this::reload);
            }
        };
        changes.startWatching();
        reload();
    }

    private static String read(File file) throws IOException {
        try (InputStreamReader reader = new InputStreamReader(new FileInputStream(file), StandardCharsets.UTF_8)) {
            StringBuilder contents = new StringBuilder();
            char[] buffer = new char[8192];
            int count;
            while ((count = reader.read(buffer)) != -1) contents.append(buffer, 0, count);
            return contents.toString();
        }
    }

    private void reload() {
        if (closed) return;
        File settings = new File(directory, "settings.json");
        if (!settings.isFile()) return;
        try {
            JSONObject options = new JSONObject(read(settings));
            String next = options.optBoolean("enabled") ? options.getString("track") : "";
            if (!next.equals(track)) {
                if (!next.isEmpty() && !next.matches("[A-Za-z0-9-]{1,48}")) return;
                cues = next.isEmpty() ? new JSONArray() : new JSONArray(read(new File(directory, next + ".json")));
                // latestEnds[i] includes cues that started earlier and overlap cue i.
                latestEnds = new long[cues.length()];
                long latestEnd = 0;
                for (int i = 0; i < cues.length(); i++) {
                    latestEnd = Math.max(latestEnd, cues.getJSONArray(i).getLong(1));
                    latestEnds[i] = latestEnd;
                }
                track = next;
                label.setText("");
                label.setVisibility(View.GONE);
            }
            label.setTextSize(Math.max(12, Math.min(48, options.optInt("size", 22))));
            video.removeCallbacks(tick);
            tick.run();
        } catch (IOException | JSONException error) {
            android.util.Log.w("USBPlayer", "Could not load captions", error);
        }
    }

    private final Runnable tick = new Runnable() {
        @Override public void run() {
            if (closed || cues.length() == 0) return;
            int time = video.getCurrentPosition();
            int low = 0, high = cues.length();
            while (low < high) {
                int mid = (low + high) >>> 1;
                if (latestEnds[mid] <= time) low = mid + 1;
                else high = mid;
            }
            StringBuilder text = new StringBuilder();
            for (int i = low; i < cues.length(); i++) {
                JSONArray cue = cues.optJSONArray(i);
                if (cue.optLong(0) > time) break;
                if (cue.optLong(1) > time) {
                    if (text.length() > 0) text.append('\n');
                    text.append(cue.optString(2));
                }
            }
            if (!TextUtils.equals(label.getText(), text)) {
                label.setText(text.toString());
                label.setVisibility(text.length() == 0 ? View.GONE : View.VISIBLE);
            }
            video.postDelayed(this, 100);
        }
    };

    void close() {
        closed = true;
        changes.stopWatching();
        video.removeCallbacks(tick);
    }

    static void delete(File directory) {
        File[] files = directory.listFiles();
        if (files != null) {
            for (File file : files) file.delete();
        }
        directory.delete();
    }
}
