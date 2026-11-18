package com.shotkam.android;

import android.content.ContentResolver;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.util.Log;

import java.io.File;
import java.io.FileDescriptor;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.RandomAccessFile;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.util.Calendar;
import java.util.TimeZone;

/**
 * ShotKam Gen 3 clock workaround, patched into the official app.
 *
 * The camera backup clock keeps only the years 2017-2025 across a power-off.
 * The patched app sets the camera clock to the real time minus LAG years and
 * adds LAG years back to every date that it reads from the camera.
 * LAG is a multiple of 4, so the leap-year pattern stays the same.
 */
public final class LagFix {
    public static final int LAG = 8;
    /**
     * Real year in which this LAG was first used. Change it together with LAG
     * (e.g. LAG = 12, LAG_SINCE = 2034), so clips from the old lag or from a
     * clock reset are not shifted by the new value.
     */
    public static final int LAG_SINCE = 2026;
    /** Year that the camera shows after a clock reset (2017-01-01). Never shifted. */
    private static final int RESET_YEAR = 2017;
    /** First year that the lagged camera clock can show. */
    public static final int FIRST_LAGGED_YEAR = Math.max(LAG_SINCE - LAG, RESET_YEAR + 1);
    private static final String TAG = "LagFix";
    /** Seconds from 1904-01-01 (MP4 epoch) to 1970-01-01. */
    private static final long MP4_EPOCH_OFFSET = 2082844800L;

    private LagFix() {
    }

    /** Clock value for the camera: the real local time minus LAG years. */
    public static Calendar now() {
        Calendar c = Calendar.getInstance();
        c.add(Calendar.YEAR, -LAG);
        Log.d(TAG, "camera clock set " + LAG + " years back");
        return c;
    }

    /** True if a camera year comes from the lagged clock. */
    static boolean isLagged(int year) {
        int hi = Calendar.getInstance().get(Calendar.YEAR) - LAG;
        return year >= FIRST_LAGGED_YEAR && year <= Math.max(hi, FIRST_LAGGED_YEAR);
    }

    /** A camera date parsed in local time: add LAG years if it comes from the lagged clock. */
    public static long shiftParsed(long millis) {
        Calendar c = Calendar.getInstance();
        c.setTimeInMillis(millis);
        if (!isLagged(c.get(Calendar.YEAR))) {
            return millis;
        }
        c.add(Calendar.YEAR, LAG);
        return c.getTimeInMillis();
    }

    /** Fix the MP4 header dates of a file in app storage. Never throws. */
    public static void fixFile(File file) {
        try (RandomAccessFile raf = new RandomAccessFile(file, "rw")) {
            int n = patch(raf.getChannel());
            Log.d(TAG, "fixFile " + file + ": " + n + " dates fixed");
        } catch (Throwable t) {
            Log.w(TAG, "fixFile " + file + " failed", t);
        }
    }

    /**
     * Fix the MP4 header dates of a MediaStore item. Never throws.
     * A FileInputStream channel is read-only and a FileOutputStream channel is
     * write-only, so the same "rw" descriptor gets one stream of each kind.
     * The streams do not own the descriptor; closing the pfd closes it.
     */
    public static void fixMp4(ContentResolver resolver, Uri uri) {
        try (ParcelFileDescriptor pfd = resolver.openFileDescriptor(uri, "rw")) {
            FileDescriptor fd = pfd.getFileDescriptor();
            FileChannel rd = new FileInputStream(fd).getChannel();
            FileChannel wr = new FileOutputStream(fd).getChannel();
            int n = patch(rd, wr);
            wr.force(false);
            Log.d(TAG, "fixMp4 " + uri + ": " + n + " dates fixed");
        } catch (Throwable t) {
            Log.w(TAG, "fixMp4 " + uri + " failed", t);
        }
    }

    /**
     * Add LAG years to the creation and modification times in mvhd, tkhd and mdhd.
     * The camera writes local time as if it were UTC, so the shift is done in UTC.
     * Returns the number of changed time fields.
     */
    public static int patch(FileChannel ch) throws IOException {
        return patch(ch, ch);
    }

    /** Same as {@link #patch(FileChannel)}, with separate channels for reading and writing. */
    public static int patch(FileChannel rd, FileChannel wr) throws IOException {
        return walk(rd, wr, 0, rd.size(), 0);
    }

    private static int walk(FileChannel ch, FileChannel wr, long start, long end, int depth)
            throws IOException {
        int changed = 0;
        long pos = start;
        ByteBuffer head = ByteBuffer.allocate(16);
        while (pos + 8 <= end) {
            head.clear().limit(8);
            if (readFully(ch, head, pos) < 8) {
                break;
            }
            long size = head.getInt(0) & 0xFFFFFFFFL;
            String type = new String(head.array(), 4, 4, "ISO-8859-1");
            int hdr = 8;
            if (size == 1) {
                head.clear().limit(8);
                readFully(ch, head, pos + 8);
                size = head.getLong(0);
                hdr = 16;
            } else if (size == 0) {
                size = end - pos;
            }
            if (size < hdr || pos + size > end) {
                break;
            }
            long body = pos + hdr;
            switch (type) {
                case "moov":
                case "trak":
                case "mdia":
                    if (depth < 4) {
                        changed += walk(ch, wr, body, pos + size, depth + 1);
                    }
                    break;
                case "mvhd":
                case "tkhd":
                case "mdhd":
                    changed += fixTimes(ch, wr, body);
                    break;
                default:
                    break;
            }
            pos += size;
        }
        return changed;
    }

    private static int fixTimes(FileChannel ch, FileChannel wr, long body) throws IOException {
        ByteBuffer b = ByteBuffer.allocate(20);
        int version = readFully(ch, b, body) >= 12 ? b.get(0) : -1;
        if ((version != 0 && version != 1) || (version == 1 && b.position() < 20)) {
            return 0;  // truncated or unknown box: leave it alone
        }
        int changed = 0;
        for (int i = 0; i < 2; i++) {
            if (version == 1) {
                int off = 4 + i * 8;
                long t = b.getLong(off);
                long nt = shiftMp4(t);
                if (nt != t) {
                    ByteBuffer w = ByteBuffer.allocate(8).putLong(0, nt);
                    writeFully(wr, w, body + off);
                    changed++;
                }
            } else {
                int off = 4 + i * 4;
                long t = b.getInt(off) & 0xFFFFFFFFL;
                long nt = shiftMp4(t);
                if (nt != t && nt <= 0xFFFFFFFFL) {
                    ByteBuffer w = ByteBuffer.allocate(4).putInt(0, (int) nt);
                    writeFully(wr, w, body + off);
                    changed++;
                }
            }
        }
        return changed;
    }

    private static long shiftMp4(long secs1904) {
        if (secs1904 == 0) {
            return 0;
        }
        Calendar c = Calendar.getInstance(TimeZone.getTimeZone("UTC"));
        c.setTimeInMillis((secs1904 - MP4_EPOCH_OFFSET) * 1000L);
        if (!isLagged(c.get(Calendar.YEAR))) {
            return secs1904;
        }
        c.add(Calendar.YEAR, LAG);
        return c.getTimeInMillis() / 1000L + MP4_EPOCH_OFFSET;
    }

    private static void writeFully(FileChannel ch, ByteBuffer b, long pos) throws IOException {
        int total = 0;
        while (b.hasRemaining()) {
            total += ch.write(b, pos + total);
        }
    }

    private static int readFully(FileChannel ch, ByteBuffer b, long pos) throws IOException {
        int total = 0;
        while (b.hasRemaining()) {
            int n = ch.read(b, pos + total);
            if (n < 0) {
                break;
            }
            total += n;
        }
        return total;
    }
}
