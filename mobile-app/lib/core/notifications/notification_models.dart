/// Notification models for handling Firebase Cloud Messaging
/// Includes models for notification payloads, states, and events

/// Represents a push notification message from FCM
class NotificationMessage {
  final String? messageId;
  final String? title;
  final String? body;
  final Map<String, dynamic> data;
  final DateTime receivedAt;
  final NotificationType type;

  NotificationMessage({
    this.messageId,
    this.title,
    this.body,
    required this.data,
    required this.receivedAt,
    this.type = NotificationType.general,
  });

  factory NotificationMessage.fromRemoteMessage(dynamic message) {
    return NotificationMessage(
      messageId: message.messageId,
      title: message.notification?.title,
      body: message.notification?.body,
      data: Map<String, dynamic>.from(message.data ?? {}),
      receivedAt: DateTime.now(),
      type: _parseNotificationType(message.data?['type'] ?? ''),
    );
  }

  static NotificationType _parseNotificationType(String type) {
    switch (type.toLowerCase()) {
      case 'order':
        return NotificationType.order;
      case 'promotion':
        return NotificationType.promotion;
      case 'account':
        return NotificationType.account;
      case 'alert':
        return NotificationType.alert;
      default:
        return NotificationType.general;
    }
  }

  @override
  String toString() => 'NotificationMessage('
      'id: $messageId, '
      'title: $title, '
      'type: $type, '
      'receivedAt: $receivedAt'
      ')';
}

/// Types of notifications
enum NotificationType {
  order,
  promotion,
  account,
  alert,
  general,
}

/// Device token status
enum DeviceTokenStatus {
  notGenerated,
  generated,
  stale,
  synced,
  error,
}

/// Notification permission status
enum NotificationPermissionStatus {
  notDetermined,
  denied,
  authorized,
  provisional,
  ephemeral,
}

/// Notification state for state management
class NotificationState {
  final bool isEnabled;
  final String? currentDeviceToken;
  final DeviceTokenStatus tokenStatus;
  final NotificationPermissionStatus permissionStatus;
  final List<String> subscribedTopics;
  final List<NotificationMessage> recentNotifications;

  const NotificationState({
    this.isEnabled = false,
    this.currentDeviceToken,
    this.tokenStatus = DeviceTokenStatus.notGenerated,
    this.permissionStatus = NotificationPermissionStatus.notDetermined,
    this.subscribedTopics = const [],
    this.recentNotifications = const [],
  });

  NotificationState copyWith({
    bool? isEnabled,
    String? currentDeviceToken,
    DeviceTokenStatus? tokenStatus,
    NotificationPermissionStatus? permissionStatus,
    List<String>? subscribedTopics,
    List<NotificationMessage>? recentNotifications,
  }) {
    return NotificationState(
      isEnabled: isEnabled ?? this.isEnabled,
      currentDeviceToken: currentDeviceToken ?? this.currentDeviceToken,
      tokenStatus: tokenStatus ?? this.tokenStatus,
      permissionStatus: permissionStatus ?? this.permissionStatus,
      subscribedTopics: subscribedTopics ?? this.subscribedTopics,
      recentNotifications: recentNotifications ?? this.recentNotifications,
    );
  }
}

/// Event for notification-related actions
abstract class NotificationEvent {
  const NotificationEvent();
}

/// Notification received event
class NotificationReceivedEvent extends NotificationEvent {
  final NotificationMessage message;
  final NotificationState state;

  const NotificationReceivedEvent({
    required this.message,
    required this.state,
  });
}

/// Notification tapped event
class NotificationTappedEvent extends NotificationEvent {
  final NotificationMessage message;

  const NotificationTappedEvent({required this.message});
}

/// Device token updated event
class DeviceTokenUpdatedEvent extends NotificationEvent {
  final String newToken;
  final String? oldToken;

  const DeviceTokenUpdatedEvent({
    required this.newToken,
    this.oldToken,
  });
}

/// Permission granted event
class PermissionGrantedEvent extends NotificationEvent {
  final NotificationPermissionStatus status;

  const PermissionGrantedEvent({required this.status});
}

/// Topic subscription changed event
class TopicSubscriptionChangedEvent extends NotificationEvent {
  final String topic;
  final bool subscribed;

  const TopicSubscriptionChangedEvent({
    required this.topic,
    required this.subscribed,
  });
}
