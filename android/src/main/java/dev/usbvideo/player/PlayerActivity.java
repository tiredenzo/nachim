package dev.usbvideo.player;

import android.app.Activity;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.media.MediaPlayer;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.view.GestureDetector;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.view.accessibility.AccessibilityManager;
import android.widget.FrameLayout;
import android.widget.MediaController;
import android.widget.TextView;
import android.widget.Toast;
import android.widget.VideoView;

import java.io.File;
import java.util.regex.Pattern;

public final class PlayerActivity extends Activity {
    private static final Pattern ID = Pattern.compile("[0-9a-f]{32}");
    private final Handler handler = new Handler();
    private SharedPreferences state;
    private FrameLayout screen;
    private VideoView video;
    private MediaController controls;
    private Captions captions;
    private String requested;
    private MediaPlayer media;

    // Require the shell permission so ordinary apps cannot control playback.
    private final BroadcastReceiver timeline = new BroadcastReceiver() {
        @Override public void onReceive(Context context, Intent intent) {
            if (media == null || !requested.equals(active())) return;
            int duration = video.getDuration();
            if (duration <= 0) return;

            String action = intent.getStringExtra("command");
            if (action != null) {
                if (!requested.equals(intent.getStringExtra("id"))) return;
                switch (action) {
                    case "seek":
                        int target = intent.getIntExtra("position", -1);
                        if (target < 0 || target >= duration) return;
                        seekTo(target);
                        break;
                    case "play": video.start(); break;
                    case "pause": video.pause(); savePlayback(); break;
                    default: return;
                }
            }
            setResultCode(RESULT_OK);
            setResultData(requested + " " + video.getCurrentPosition() + " " + duration + " "
                    + (video.isPlaying() ? "1" : "0"));
        }
    };

    private final Runnable checkpoint = new Runnable() {
        @Override public void run() {
            savePlayback();
            handler.postDelayed(this, 1000);
        }
    };

    @Override public void onCreate(Bundle saved) {
        super.onCreate(saved);
        state = getSharedPreferences("playback", MODE_PRIVATE);
        screen = new FrameLayout(this);
        screen.setBackgroundColor(Color.BLACK);
        screen.setFitsSystemWindows(true);
        setContentView(screen);
        requested = saved == null ? acceptUpload(getIntent()) : saved.getString("requested");
        if (requested == null) requested = active();

        // An interrupted handoff can leave files here. Keep both the active and incoming videos.
        File[] files = getFilesDir().listFiles();
        if (files != null) {
            for (File file : files) {
                String name = file.getName();
                if (name.matches("[0-9a-f]{32}\\.mp4") &&
                        !name.equals(active() + ".mp4") && !name.equals(requested + ".mp4")) {
                    file.delete();
                } else if (name.matches("cc-[0-9a-f]{32}") && !name.equals("cc-" + active())
                        && !name.equals("cc-" + requested)) {
                    Captions.delete(file);
                }
            }
        }
    }

    private String active() { return state.getString("id", null); }
    private File movie(String id) { return new File(getFilesDir(), id + ".mp4"); }

    private File captionDir(String id) { return new File(getFilesDir(), "cc-" + id); }

    private void deleteVideo(String id) {
        movie(id).delete();
        Captions.delete(captionDir(id));
    }

    private String acceptUpload(Intent intent) {
        if (intent.getComponent() == null ||
                !intent.getComponent().getClassName().equals(getPackageName() + ".Receive")) return null;
        String id = intent.getStringExtra("id");
        if (id == null || !ID.matcher(id).matches()) return null;
        File uploaded = new File(getCacheDir(), "ready-" + id + ".mp4");
        File staged = new File(getCacheDir(), "cc-" + id);
        if (staged.exists() && !staged.renameTo(captionDir(id))) return null;
        return uploaded.renameTo(movie(id)) ? id : null;
    }

    @Override protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        String next = acceptUpload(intent);
        if (next == null) return;
        if (requested != null && !requested.equals(active())) deleteVideo(requested);
        requested = next;
    }

    @Override protected void onSaveInstanceState(Bundle out) {
        super.onSaveInstanceState(out);
        out.putString("requested", requested);
    }

    @Override protected void onResume() {
        super.onResume();
        IntentFilter filter = new IntentFilter(getPackageName() + ".TIMELINE");
        if (Build.VERSION.SDK_INT >= 33) {
            registerReceiver(timeline, filter, "android.permission.DUMP", null, Context.RECEIVER_EXPORTED);
        } else {
            registerReceiver(timeline, filter, "android.permission.DUMP", null);
        }
        if (video == null) showVideo();
    }

    @Override protected void onPause() {
        unregisterReceiver(timeline);
        savePlayback();
        releaseVideo();
        super.onPause();
    }

    private void savePlayback() {
        if (media != null && requested.equals(active())) {
            int position = video.getCurrentPosition();
            if (position >= 0) {
                state.edit().putInt("position", position).putBoolean("playing", video.isPlaying()).apply();
            }
        }
    }

    private void seekTo(int position) {
        if (Build.VERSION.SDK_INT >= 26) media.seekTo(position, MediaPlayer.SEEK_CLOSEST);
        else video.seekTo(position);
    }

    private void releaseVideo() {
        handler.removeCallbacks(checkpoint);
        media = null;
        if (controls != null) {
            controls.hide();
            controls = null;
        }
        if (captions != null) {
            captions.close();
            captions = null;
        }
        if (video != null) {
            VideoView old = video;
            video = null;
            old.stopPlayback();
            screen.removeAllViews();
        }
    }

    private static void setFullscreen(View decor, boolean fullscreen) {
        if (Build.VERSION.SDK_INT >= 30) {
            WindowInsetsController bars = decor.getWindowInsetsController();
            if (fullscreen) {
                bars.setSystemBarsBehavior(WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE);
                bars.hide(WindowInsets.Type.systemBars());
            } else {
                bars.show(WindowInsets.Type.systemBars());
            }
        } else {
            int flags = View.SYSTEM_UI_FLAG_FULLSCREEN | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                    | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY;
            int current = decor.getSystemUiVisibility();
            decor.setSystemUiVisibility(fullscreen ? current | flags : current & ~flags);
        }
    }

    private void message(String text) {
        setFullscreen(getWindow().getDecorView(), false);
        screen.removeAllViews();
        TextView label = new TextView(this);
        label.setGravity(Gravity.CENTER);
        label.setText(text);
        screen.addView(label);
    }

    private void addPlaybackControls(VideoView player) {
        controls = new MediaController(this) {
            private final Runnable fadeOut = this::hide;

            @Override public void show(int timeout) {
                handler.removeCallbacks(fadeOut);
                // View-posted timeouts can survive hide() on a detached window's handler.
                super.show(0);
                if (timeout != 0 && isShowing()
                        && !getContext().getSystemService(AccessibilityManager.class).isTouchExplorationEnabled()) {
                    handler.postDelayed(fadeOut, timeout);
                }
            }

            @Override public void hide() {
                handler.removeCallbacks(fadeOut);
                super.hide();
            }

            @Override protected void onAttachedToWindow() {
                super.onAttachedToWindow();
                // From Android 14, the activity controls both bars while this panel has focus.
                if (Build.VERSION.SDK_INT < 34) setFullscreen(getRootView(), true);
            }
        };
        // Keep the time counters clear of rounded screen corners.
        float density = getResources().getDisplayMetrics().density;
        int sidePadding = Math.round(24 * density);
        controls.setPadding(sidePadding, 0, sidePadding, Math.round(12 * density));
        player.setMediaController(controls);
        // Handle taps above VideoView: some Android versions toggle controls even when they return false.
        View tapArea = new View(this);
        tapArea.setContentDescription("Show or hide playback controls");
        tapArea.setOnClickListener(v -> {
            if (player != video || media == null) return;
            if (controls.isShowing()) controls.hide();
            else controls.show();
        });
        GestureDetector taps = new GestureDetector(this, new GestureDetector.SimpleOnGestureListener() {
            @Override public boolean onSingleTapUp(MotionEvent event) {
                return tapArea.performClick();
            }
        });
        taps.setOnDoubleTapListener(null); // Each tap toggles immediately, including a quick second tap.
        tapArea.setOnTouchListener((v, event) -> {
            taps.onTouchEvent(event);
            return true; // Don't let View turn an in-bounds swipe into a click on release.
        });
        screen.addView(tapArea);
    }

    private void showVideo() {
        if (requested == null) {
            message("Ready for a video from your Mac.");
            return;
        }
        final String id = requested;
        setFullscreen(getWindow().getDecorView(), true);
        final VideoView player = new VideoView(this);
        video = player;
        screen.removeAllViews();
        // With two exact bounds VideoView fits the frame to the source aspect ratio.
        screen.addView(player, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT, Gravity.CENTER));
        captions = new Captions(screen, player, captionDir(id));
        addPlaybackControls(player);
        player.setOnPreparedListener(mp -> {
            if (player != video) return;
            media = mp;
            boolean restoring = id.equals(active());
            if (restoring) seekTo(state.getInt("position", 0));
            if (!restoring || state.getBoolean("playing", true)) player.start();
            handler.postDelayed(checkpoint, 1000);
        });
        player.setOnInfoListener((mp, what, extra) -> {
            // Keep the previous video until its replacement renders a frame and its ID is saved.
            if (player == video && what == MediaPlayer.MEDIA_INFO_VIDEO_RENDERING_START && !id.equals(active())) {
                String previous = active();
                if (state.edit().putString("id", id).putInt("position", 0).putBoolean("playing", true).commit() && previous != null) {
                    deleteVideo(previous);
                }
            }
            return false;
        });
        player.setOnCompletionListener(mp -> {
            if (player != video) return;
            releaseVideo();
            if (id.equals(active())) state.edit().clear().commit();
            deleteVideo(id);
            requested = active();
            showVideo();
        });
        player.setOnErrorListener((mp, what, extra) -> {
            if (player != video) return true;
            releaseVideo();
            if (!id.equals(active())) {
                deleteVideo(id);
                requested = active();
                Toast.makeText(this, "Could not play the new video.", Toast.LENGTH_LONG).show();
                showVideo();
            } else {
                message("Could not play this video. You can send another from your Mac.");
            }
            return true;
        });
        player.setVideoPath(movie(id).getAbsolutePath());
    }
}
