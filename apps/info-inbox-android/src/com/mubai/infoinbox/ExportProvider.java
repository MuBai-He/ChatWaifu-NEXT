package com.mubai.infoinbox;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;
import java.io.File;
import java.io.FileNotFoundException;

/** One explicitly shared export file; no directory traversal or general file access. */
public final class ExportProvider extends ContentProvider {
  public static final Uri URI = Uri.parse("content://com.mubai.infoinbox.exports/memories.json");

  @Override
  public boolean onCreate() {
    return true;
  }

  private File export(Uri uri) throws FileNotFoundException {
    if (!URI.equals(uri)) throw new FileNotFoundException();
    return new File(getContext().getFilesDir(), "exports/memories.json");
  }

  @Override
  public String getType(Uri uri) {
    return "application/json";
  }

  @Override
  public ParcelFileDescriptor openFile(Uri uri, String mode) throws FileNotFoundException {
    if (!"r".equals(mode)) throw new FileNotFoundException("Read-only export");
    return ParcelFileDescriptor.open(export(uri), ParcelFileDescriptor.MODE_READ_ONLY);
  }

  @Override
  public Cursor query(
      Uri uri, String[] projection, String selection, String[] args, String sortOrder) {
    try {
      File file = export(uri);
      String[] cols =
          projection == null
              ? new String[] {OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE}
              : projection;
      MatrixCursor c = new MatrixCursor(cols);
      Object[] row = new Object[cols.length];
      for (int i = 0; i < cols.length; i++) {
        if (OpenableColumns.DISPLAY_NAME.equals(cols[i])) row[i] = "memories.json";
        if (OpenableColumns.SIZE.equals(cols[i])) row[i] = file.length();
      }
      c.addRow(row);
      return c;
    } catch (FileNotFoundException e) {
      return null;
    }
  }

  @Override
  public Uri insert(Uri uri, ContentValues values) {
    throw new UnsupportedOperationException();
  }

  @Override
  public int update(Uri uri, ContentValues values, String s, String[] a) {
    throw new UnsupportedOperationException();
  }

  @Override
  public int delete(Uri uri, String s, String[] a) {
    throw new UnsupportedOperationException();
  }
}
