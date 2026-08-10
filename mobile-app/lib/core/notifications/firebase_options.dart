// Sample Firebase options for open-source distribution.
// Replace these placeholder values with your own Firebase project details.
// Placeholder config files are provided at:
//   - android/app/google-services.json
//   - ios/Runner/GoogleService-Info.plist

import 'dart:io';
import 'package:firebase_core/firebase_core.dart';

class FirebasePlaceholderConfig {
  static const apiKey = 'A12345678901234567890123456789012345678';
  static const senderId = '000000000000';
  static const projectId = 'placeholder-firebase-project';
  static const storageBucket = 'placeholder-firebase-project.appspot.com';
  static const androidAppId = '1:000000000000:android:1234567890abcdef12345678';
  static const iosAppId = '1:000000000000:ios:1234567890abcdef12345678';
  static const iosBundleId = 'com.webkul.bagistoApp.iOS';
}

class DefaultFirebaseOptions {
  static FirebaseOptions get currentPlatform {
    if (Platform.isAndroid) {
      return android;
    }
    if (Platform.isIOS) {
      return ios;
    }
    throw UnsupportedError(
      'DefaultFirebaseOptions are not supported for this platform.',
    );
  }

  static const FirebaseOptions android = FirebaseOptions(
    apiKey: FirebasePlaceholderConfig.apiKey,
    appId: FirebasePlaceholderConfig.androidAppId,
    messagingSenderId: FirebasePlaceholderConfig.senderId,
    projectId: FirebasePlaceholderConfig.projectId,
    storageBucket: FirebasePlaceholderConfig.storageBucket,
  );

  static const FirebaseOptions ios = FirebaseOptions(
    apiKey: FirebasePlaceholderConfig.apiKey,
    appId: FirebasePlaceholderConfig.iosAppId,
    messagingSenderId: FirebasePlaceholderConfig.senderId,
    projectId: FirebasePlaceholderConfig.projectId,
    storageBucket: FirebasePlaceholderConfig.storageBucket,
    iosBundleId: FirebasePlaceholderConfig.iosBundleId,
  );
}
