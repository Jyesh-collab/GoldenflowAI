import 'package:flutter/foundation.dart';
import 'package:flutter_uxcam/flutter_uxcam.dart';
import '../constants/analytics_constants.dart';

/// Thin wrapper around the UXCam SDK.
///
/// Every call is a no-op until [initialize] actually starts a session, which
/// happens only when a real [uxcamAppKey] has been configured and the app
/// isn't pointed at a local/dev backend — this keeps placeholder builds and
/// local testing sessions out of the UXCam dashboard.
class UxcamService {
  UxcamService._();

  static bool _started = false;

  static bool get isStarted => _started;

  /// Starts the UXCam session. Call once, early in `main()`.
  static Future<void> initialize() async {
    if (!isUxcamAppKeyConfigured) {
      debugPrint(
        '📊 UXCam disabled — uxcamAppKey is still a placeholder '
        '(set it in analytics_constants.dart).',
      );
      return;
    }
    if (isLocalDevBackend) {
      debugPrint(
        '📊 UXCam disabled — app is pointed at a local/dev backend.',
      );
      return;
    }

    try {
      final config = FlutterUxConfig(
        userAppKey: uxcamAppKey,
        enableAutomaticScreenNameTagging: false,
        enableAdvancedGestureRecognition: true,
        enableCrashHandling: true,
      );
      _started = await FlutterUxcam.startWithConfiguration(config);
      debugPrint(_started ? '📊 UXCam started' : '📊 UXCam failed to start');
    } catch (e) {
      debugPrint('📊 UXCam init error: $e');
    }
  }

  /// Tags the currently visible screen with a human-readable name.
  /// Automatic route-name tagging is off (see [initialize]) since this app
  /// doesn't use named routes, so screens call this explicitly.
  static void tagScreen(String screenName) {
    if (!_started) return;
    FlutterUxcam.tagScreenName(screenName);
  }

  /// Occludes (blurs) the current screen from session recordings.
  /// Used for screens with sensitive input — login, checkout, addresses.
  static void setSensitiveScreen(bool hide) {
    if (!_started) return;
    FlutterUxcam.occludeSensitiveScreen(hide);
  }

  /// Associates the current session with a logged-in user.
  /// Call on successful login/registration; call [clearUserIdentity] on logout.
  static void identifyUser({
    required String userId,
    String? name,
    String? email,
  }) {
    if (!_started) return;
    FlutterUxcam.setUserIdentity(userId);
    if (name != null && name.isNotEmpty) {
      FlutterUxcam.setUserProperty('name', name);
    }
    if (email != null && email.isNotEmpty) {
      FlutterUxcam.setUserProperty('email', email);
    }
  }

  /// Resets user identity. Call on logout so the next session isn't
  /// misattributed to the previous user.
  static void clearUserIdentity() {
    if (!_started) return;
    FlutterUxcam.setUserIdentity(null);
  }

  /// Logs a named journey/business event, optionally with properties.
  static void logEvent(String name, [Map<String, dynamic>? properties]) {
    if (!_started) return;
    if (properties == null || properties.isEmpty) {
      FlutterUxcam.logEvent(name);
    } else {
      FlutterUxcam.logEventWithProperties(name, properties);
    }
  }
}
