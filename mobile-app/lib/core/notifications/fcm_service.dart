import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'device_token_service.dart';

/// iOS-specific retry configuration
const int _iosTokenRetryAttempts = 5;
const Duration _iosTokenRetryDelay = Duration(seconds: 2);

/// Callback type for notification handling
typedef NotificationCallback = Future<void> Function(RemoteMessage message);

/// Callback type for local notification tap handling
typedef LocalNotificationCallback =
    Future<void> Function(Map<String, dynamic> data);

/// Firebase Cloud Messaging (FCM) notification service
/// Handles push notifications for foreground, background, and terminated states
class FCMService {
  static final FCMService _instance = FCMService._internal();

  factory FCMService() {
    return _instance;
  }

  FCMService._internal();

  final FirebaseMessaging _messaging = FirebaseMessaging.instance;
  late FlutterLocalNotificationsPlugin _localNotificationsPlugin;

  // Callbacks for different notification states
  NotificationCallback? _onForegroundMessage;
  NotificationCallback? _onMessageOpenedApp;
  LocalNotificationCallback? _onLocalNotificationTapped;

  /// Initialize FCM service
  /// - Request notification permissions
  /// - Set up notification handlers
  /// - Retrieve and save device token
  Future<void> initialize({
    NotificationCallback? onForegroundMessage,
    NotificationCallback? onBackgroundMessage,
    NotificationCallback? onMessageOpenedApp,
    LocalNotificationCallback? onLocalNotificationTapped,
  }) async {
    try {
      debugPrint('🔔 Initializing FCM...');

      _onForegroundMessage = onForegroundMessage;
      _onMessageOpenedApp = onMessageOpenedApp;
      _onLocalNotificationTapped = onLocalNotificationTapped;

      // onBackgroundMessage is handled by firebaseMessagingBackgroundHandler
      // which is a top-level function

      // Request notification permissions
      await _requestNotificationPermissions();

      // Initialize local notifications
      await _initializeLocalNotifications();

      // Handle foreground messages
      FirebaseMessaging.onMessage.listen(_handleForegroundMessage);

      // Handle background messages (when app is in background)
      FirebaseMessaging.onMessageOpenedApp.listen(_handleMessageOpenedApp);

      // Handle terminated state (app opened from notification)
      final initialMessage = await _messaging.getInitialMessage();
      if (initialMessage != null) {
        debugPrint('📱 App opened from terminated state notification');
        await _handleMessageOpenedApp(initialMessage);
      }

      // Retrieve and save device token
      final token = await _getAndSaveDeviceToken();

      // Subscribe to app topic only if we have a token
      if (token != null && token.isNotEmpty) {
        await subscribeToTopic('bagisto_mobikul');
      } else {
        debugPrint('⚠️ Skipping topic subscription - no device token yet');
        // Will subscribe when token is available
      }

      // Listen for token refresh
      _messaging.onTokenRefresh.listen((newToken) async {
        await _handleTokenRefresh(newToken);
        // Subscribe to topic when token is refreshed
        await subscribeToTopic('bagisto_mobikul');
      });

      debugPrint('✅ FCM initialized successfully');
    } catch (e) {
      debugPrint('❌ FCM initialization error: $e');
      rethrow;
    }
  }

  /// Request notification permissions (especially for iOS 10+)
  Future<void> _requestNotificationPermissions() async {
    try {
      debugPrint('📋 Requesting notification permissions...');

      final NotificationSettings settings = await _messaging.requestPermission(
        alert: true,
        announcement: true,
        badge: true,
        carPlay: false,
        criticalAlert: false,
        provisional: true,
        sound: true,
      );

      debugPrint('🔐 Permission status: ${settings.authorizationStatus}');

      if (settings.authorizationStatus == AuthorizationStatus.authorized) {
        debugPrint('✅ Notification permissions granted');
      } else if (settings.authorizationStatus ==
          AuthorizationStatus.provisional) {
        debugPrint('⚠️ Provisional notification permissions granted');
      } else {
        debugPrint('❌ Notification permissions denied');
      }
    } catch (e) {
      debugPrint('❌ Error requesting notification permissions: $e');
    }
  }

  /// Initialize local notifications plugin for displaying notifications
  Future<void> _initializeLocalNotifications() async {
    try {
      _localNotificationsPlugin = FlutterLocalNotificationsPlugin();

      // Android initialization
      const AndroidInitializationSettings androidSettings =
          AndroidInitializationSettings('@mipmap/ic_launcher');

      // iOS initialization
      final DarwinInitializationSettings
      iosSettings = DarwinInitializationSettings(
        requestAlertPermission: true,
        requestBadgePermission: true,
        requestSoundPermission: true,
        onDidReceiveLocalNotification:
            (int id, String? title, String? body, String? payload) async {
              debugPrint('📲 Local notification received on device');
            },
      );

      final InitializationSettings initSettings = InitializationSettings(
        android: androidSettings,
        iOS: iosSettings,
      );

      await _localNotificationsPlugin.initialize(
        initSettings,
        onDidReceiveNotificationResponse: (NotificationResponse response) {
          debugPrint('🔔 Local notification tapped');

          // Parse the payload and call the callback if available
          if (response.payload != null && _onLocalNotificationTapped != null) {
            final payload = response.payload!;
            // Parse the payload string back to a map
            // Format: status=closed/orderId=575/...
            final data = _parseNotificationPayload(payload);
            _onLocalNotificationTapped!(data);
          }
        },
      );

      // Create Android notification channel
      await _createAndroidNotificationChannel();

      debugPrint('✅ Local notifications initialized');
    } catch (e) {
      debugPrint('❌ Error initializing local notifications: $e');
    }
  }

  /// Create Android notification channel
  Future<void> _createAndroidNotificationChannel() async {
    if (!Platform.isAndroid) return;

    const AndroidNotificationChannel channel = AndroidNotificationChannel(
      'bagisto_notifications',
      'Bagisto Notifications',
      description: 'This channel is used for Bagisto app notifications',
      importance: Importance.max,
      enableVibration: true,
      playSound: true,
    );

    await _localNotificationsPlugin
        .resolvePlatformSpecificImplementation<
          AndroidFlutterLocalNotificationsPlugin
        >()
        ?.createNotificationChannel(channel);

    debugPrint('✅ Android notification channel created');
  }

  /// Get device token and save it locally
  /// On iOS, this waits for APNS token to be available first
  Future<String?> _getAndSaveDeviceToken() async {
    try {
      debugPrint('⏳ Getting device token...');

      // On iOS, wait for APNS token to be available
      if (Platform.isIOS) {
        final apnsToken = await _waitForApnsToken();
        if (apnsToken == null) {
          debugPrint('⚠️ APNS token not available, scheduling retry...');
          _scheduleTokenRetry();
          return null;
        }
        debugPrint('✅ APNS token obtained');
      }

      final token = await _messaging.getToken();

      if (token != null && token.isNotEmpty) {
        await DeviceTokenService.saveDeviceToken(token);
        debugPrint('✅ Device token obtained and stored');
        return token;
      } else {
        debugPrint('⚠️ Device token is empty or null');
        _scheduleTokenRetry();
        return null;
      }
    } catch (e) {
      debugPrint('⚠️ Error getting device token (will retry later): $e');
      _scheduleTokenRetry();
      return null;
    }
  }

  /// Wait for APNS token to be available on iOS
  /// Returns the APNS token or null if not available after retries
  Future<String?> _waitForApnsToken() async {
    if (!Platform.isIOS) return null;

    for (int attempt = 1; attempt <= _iosTokenRetryAttempts; attempt++) {
      try {
        debugPrint(
          '🔍 Checking APNS token (attempt $attempt/$_iosTokenRetryAttempts)...',
        );
        final apnsToken = await _messaging.getAPNSToken();
        if (apnsToken != null && apnsToken.isNotEmpty) {
          return apnsToken;
        }
      } catch (e) {
        debugPrint('⚠️ APNS token check failed: $e');
      }

      if (attempt < _iosTokenRetryAttempts) {
        await Future.delayed(_iosTokenRetryDelay);
      }
    }

    return null;
  }

  /// Schedule a retry to get the token after a delay
  void _scheduleTokenRetry() {
    debugPrint('🔄 Scheduling token retry in 5 seconds...');
    Future.delayed(const Duration(seconds: 5), () async {
      try {
        final token = await _getAndSaveDeviceToken();
        if (token != null && token.isNotEmpty) {
          debugPrint('✅ Token obtained on retry');
          await subscribeToTopic('bagisto_mobikul');
        }
      } catch (e) {
        debugPrint('⚠️ Token retry failed: $e');
      }
    });
  }

  /// Handle token refresh
  Future<void> _handleTokenRefresh(String newToken) async {
    try {
      debugPrint('🔄 FCM token refreshed');
      await DeviceTokenService.saveDeviceToken(newToken);

      // TODO: Send new token to server if user is authenticated
      // This should be handled by the auth repository
    } catch (e) {
      debugPrint('❌ Error handling token refresh: $e');
    }
  }

  /// Handle foreground messages (app is open and in focus)
  Future<void> _handleForegroundMessage(RemoteMessage message) async {
    try {
      debugPrint(
        '📬 Foreground message received '
        '(id=${message.messageId}, hasData=${message.data.isNotEmpty})',
      );

      // Display local notification
      await _displayNotification(message);

      // Call custom callback
      if (_onForegroundMessage != null) {
        await _onForegroundMessage!(message);
      }
    } catch (e) {
      debugPrint('❌ Error handling foreground message: $e');
    }
  }

  /// Handle message opened from background
  Future<void> _handleMessageOpenedApp(RemoteMessage message) async {
    try {
      debugPrint('📲 Message opened from app: ${message.messageId}');

      // Call custom callback
      if (_onMessageOpenedApp != null) {
        await _onMessageOpenedApp!(message);
      }
    } catch (e) {
      debugPrint('❌ Error handling message opened app: $e');
    }
  }

  /// Display local notification
  Future<void> _displayNotification(RemoteMessage message) async {
    try {
      final notification = message.notification;
      if (notification == null) return;

      final androidDetails = AndroidNotificationDetails(
        'bagisto_notifications',
        'Bagisto Notifications',
        channelDescription:
            'This channel is used for Bagisto app notifications',
        importance: Importance.max,
        priority: Priority.high,
        showWhen: true,
        enableVibration: true,
        playSound: true,
        autoCancel: true,
      );

      final iosDetails = const DarwinNotificationDetails(
        presentAlert: true,
        presentBadge: true,
        presentSound: true,
      );

      final notificationDetails = NotificationDetails(
        android: androidDetails,
        iOS: iosDetails,
      );

      await _localNotificationsPlugin.show(
        message.hashCode,
        notification.title,
        notification.body,
        notificationDetails,
        payload: message.data.isNotEmpty ? jsonEncode(message.data) : null,
      );

      debugPrint('✅ Local notification displayed');
    } catch (e) {
      debugPrint('❌ Error displaying notification: $e');
    }
  }

  /// Get current device token
  Future<String?> getDeviceToken() async {
    return DeviceTokenService.getDeviceToken();
  }

  /// Manually refresh device token
  Future<String?> refreshDeviceToken() async {
    return _getAndSaveDeviceToken();
  }

  /// Enable/disable notifications
  Future<void> setNotificationsEnabled(bool enabled) async {
    try {
      if (enabled) {
        await _messaging.setAutoInitEnabled(true);
      } else {
        await _messaging.setAutoInitEnabled(false);
      }
      debugPrint('🔔 Notifications ${enabled ? 'enabled' : 'disabled'}');
    } catch (e) {
      debugPrint('❌ Error toggling notifications: $e');
    }
  }

  /// Subscribe to topic
  Future<void> subscribeToTopic(String topic) async {
    try {
      await _messaging.subscribeToTopic(topic);
      debugPrint('📮 Subscribed to topic: $topic');
    } catch (e) {
      debugPrint('❌ Error subscribing to topic: $e');
    }
  }

  /// Unsubscribe from topic
  Future<void> unsubscribeFromTopic(String topic) async {
    try {
      await _messaging.unsubscribeFromTopic(topic);
      debugPrint('📪 Unsubscribed from topic: $topic');
    } catch (e) {
      debugPrint('❌ Error unsubscribing from topic: $e');
    }
  }

  /// Parse notification payload string to map.
  /// Supports JSON format (preferred) and legacy key=value/key=value format.
  Map<String, dynamic> _parseNotificationPayload(String payload) {
    // Try JSON first (new format)
    try {
      final decoded = jsonDecode(payload);
      if (decoded is Map<String, dynamic>) {
        return decoded;
      }
    } catch (_) {
      // Not JSON, try legacy formats
    }

    final Map<String, dynamic> data = {};

    // Legacy: try slash-delimited key=value pairs (from old Uri pathSegments encoding)
    // e.g. "status=closed/orderId=575/type=order_status"
    if (payload.contains('/') && payload.contains('=')) {
      final segments = payload.split('/');
      for (final segment in segments) {
        final eqIndex = segment.indexOf('=');
        if (eqIndex > 0) {
          final key = segment.substring(0, eqIndex);
          final value = Uri.decodeComponent(segment.substring(eqIndex + 1));
          data[key] = value;
        }
      }
      if (data.isNotEmpty) return data;
    }

    // Legacy: try ampersand-delimited query string
    if (payload.contains('=') && payload.contains('&')) {
      final pairs = payload.split('&');
      for (final pair in pairs) {
        final eqIndex = pair.indexOf('=');
        if (eqIndex > 0) {
          final key = pair.substring(0, eqIndex);
          final value = Uri.decodeComponent(pair.substring(eqIndex + 1));
          data[key] = value;
        }
      }
      if (data.isNotEmpty) return data;
    }

    // Single key=value
    if (payload.contains('=')) {
      final eqIndex = payload.indexOf('=');
      if (eqIndex > 0) {
        data[payload.substring(0, eqIndex)] = Uri.decodeComponent(
          payload.substring(eqIndex + 1),
        );
      }
    }

    return data;
  }
}

/// Top-level background message handler (must be static)
/// This handles notifications when app is terminated or in background
@pragma('vm:entry-point')
Future<void> firebaseMessagingBackgroundHandler(RemoteMessage message) async {
  try {
    debugPrint(
      '🌙 Background message received '
      '(id=${message.messageId}, hasData=${message.data.isNotEmpty})',
    );

    // Initialize local notifications if needed
    final localNotificationsPlugin = FlutterLocalNotificationsPlugin();

    // Display notification even in background
    final notification = message.notification;
    if (notification != null) {
      try {
        final androidDetails = AndroidNotificationDetails(
          'bagisto_notifications',
          'Bagisto Notifications',
          channelDescription: 'Bagisto app notifications',
          importance: Importance.max,
          priority: Priority.high,
        );

        final iosDetails = const DarwinNotificationDetails(
          presentAlert: true,
          presentBadge: true,
          presentSound: true,
        );

        await localNotificationsPlugin.show(
          message.hashCode,
          notification.title,
          notification.body,
          NotificationDetails(android: androidDetails, iOS: iosDetails),
        );

        debugPrint('✅ Background notification displayed');
      } catch (e) {
        debugPrint('⚠️ Error displaying background notification: $e');
      }
    }
  } catch (e) {
    debugPrint('❌ Error handling background message: $e');
  }
}
