package top.kira.host;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;
import java.io.File;
import java.io.FileNotFoundException;

public final class UpdateApkProvider extends ContentProvider {
    private File apk(Uri uri) throws FileNotFoundException {
        if (!"/host-update.apk".equals(uri.getPath()) || uri.getQuery() != null || uri.getFragment() != null)
            throw new FileNotFoundException("Unknown update file");
        File file = new File(getContext().getFilesDir(), "host-updates/host-update.apk");
        if (!file.isFile() || java.nio.file.Files.isSymbolicLink(file.toPath()))
            throw new FileNotFoundException("Update unavailable");
        return file;
    }
    @Override public boolean onCreate() { return true; }
    @Override public String getType(Uri uri) { return "application/vnd.android.package-archive"; }
    @Override public ParcelFileDescriptor openFile(Uri uri, String mode) throws FileNotFoundException {
        if (!"r".equals(mode)) throw new FileNotFoundException("Read only");
        return ParcelFileDescriptor.open(apk(uri), ParcelFileDescriptor.MODE_READ_ONLY);
    }
    @Override public Cursor query(Uri uri, String[] projection, String selection, String[] args, String sort) {
        try {
            File file = apk(uri);
            String[] columns = projection == null ? new String[]{OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE} : projection;
            MatrixCursor cursor = new MatrixCursor(columns);
            Object[] row = new Object[columns.length];
            for (int index = 0; index < columns.length; index++) {
                if (OpenableColumns.DISPLAY_NAME.equals(columns[index])) row[index] = file.getName();
                else if (OpenableColumns.SIZE.equals(columns[index])) row[index] = file.length();
            }
            cursor.addRow(row);
            return cursor;
        } catch (FileNotFoundException ignored) { return null; }
    }
    @Override public Uri insert(Uri uri, ContentValues values) { throw new UnsupportedOperationException(); }
    @Override public int delete(Uri uri, String selection, String[] args) { throw new UnsupportedOperationException(); }
    @Override public int update(Uri uri, ContentValues values, String selection, String[] args) { throw new UnsupportedOperationException(); }
}
