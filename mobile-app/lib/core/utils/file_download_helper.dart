import 'dart:convert';
import 'dart:io';
import 'package:path_provider/path_provider.dart';
import 'package:open_filex/open_filex.dart';

class FileDownloadHelper {
  static Future<String> getFilePath(String fileName) async {
    Directory? dir;
    if (Platform.isAndroid) {
      dir = await getExternalStorageDirectory();
    } else {
      dir = await getApplicationDocumentsDirectory();
    }

    // Sanitize file name
    String sanitizedFileName = fileName.replaceAll(RegExp(r'[/\\]'), '_');
    return '${dir?.path}/$sanitizedFileName';
  }

  static Future<bool> requestPermissions() async {
    if (Platform.isAndroid) {
      // App-specific external storage does not require broad storage
      // permission on Android.
      return true;
    }

    return true;
  }

  static Future<String?> saveBase64File(
    String base64String,
    String fileName,
  ) async {
    try {
      final hasPermission = await requestPermissions();
      if (!hasPermission) {
        throw Exception('Storage permission denied');
      }

      final filePath = await getFilePath(fileName);
      final bytes = base64.decode(base64String);
      final file = File(filePath);

      if (!(await file.parent.exists())) {
        await file.parent.create(recursive: true);
      }

      await file.writeAsBytes(bytes);
      return filePath;
    } catch (e) {
      rethrow;
    }
  }

  static Future<void> openFile(String filePath) async {
    await OpenFilex.open(filePath);
  }

  static Future<void> saveAndOpen(String base64String, String fileName) async {
    final path = await saveBase64File(base64String, fileName);
    if (path != null) {
      await openFile(path);
    }
  }
}
