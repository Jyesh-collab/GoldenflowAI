import 'package:firebase_core/firebase_core.dart';
import 'package:flutter/foundation.dart';
import 'firebase_options.dart';

/// Firebase initialization service
/// Handles Firebase setup for both Android and iOS platforms
class FirebaseService {
  static final FirebaseService _instance = FirebaseService._internal();
  static bool _isEnabled = false;

  factory FirebaseService() {
    return _instance;
  }

  FirebaseService._internal();

  static bool get isEnabled => _isEnabled;

  static bool _hasPlaceholderConfig(FirebaseOptions options) {
    return options.projectId == FirebasePlaceholderConfig.projectId ||
        options.messagingSenderId == FirebasePlaceholderConfig.senderId ||
        options.storageBucket == FirebasePlaceholderConfig.storageBucket;
  }

  /// Initialize Firebase with platform-specific options
  /// Must be called before running the app
  static Future<bool> initialize() async {
    final options = DefaultFirebaseOptions.currentPlatform;

    if (_hasPlaceholderConfig(options)) {
      _isEnabled = false;
      debugPrint(
        '⚠️ Firebase config contains placeholder values. '
        'Skipping Firebase initialization.',
      );
      return false;
    }

    try {
      debugPrint('🔥 Initializing Firebase...');

      await Firebase.initializeApp(
        options: options,
      );

      _isEnabled = true;
      debugPrint('✅ Firebase initialized successfully');
      return true;
    } catch (e) {
      _isEnabled = false;
      debugPrint('❌ Firebase initialization error: $e');
      return false;
    }
  }
}
