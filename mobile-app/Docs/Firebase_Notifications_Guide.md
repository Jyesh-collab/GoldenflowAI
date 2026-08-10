# Firebase Push Notifications - Implementation Guide

## Overview

This guide explains how Firebase Push Notifications are integrated into your Bagisto Flutter app, with support for login, register, and logout flows.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                      Firebase Service                     │
│  - Initializes Firebase Core                             │
│  - Manages SDK configuration                             │
└────────────────────┬────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────┐
│                    FCM Service                            │
│  - Handles push notifications                            │
│  - Manages foreground/background/terminated states       │
│  - Displays local notifications                          │
└────────────────────┬────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────┐
│           Device Token Service                           │
│  - Stores/retrieves FCM tokens locally                   │
│  - Manages token lifecycle                              │
│  - Checks token staleness                               │
└────────────────────┬────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────┐
│          Auth Repository & Bloc                          │
│  - Sends device token on login/register                 │
│  - Clears device token on logout                        │
│  - Manages auth tokens                                  │
└─────────────────────────────────────────────────────────┘
```

## File Structure

```
lib/
├── core/
│   └── notifications/
│       ├── firebase_service.dart           # Firebase initialization
│       ├── firebase_options.dart           # Platform-specific config
│       ├── device_token_service.dart       # Token management
│       └── fcm_service.dart               # FCM handler
├── features/
│   └── auth/
│       ├── data/
│       │   └── repository/
│       │       └── auth_repository.dart    # Updated with device token
│       └── presentation/
│           └── bloc/
│               └── auth_bloc.dart          # Updated event/state
└── main.dart                               # Firebase initialization
```

## Key Components

### 1. FirebaseService
- **Purpose:** Initialize Firebase SDK
- **Called:** In `main()` before app runs
- **Key Method:** `FirebaseService.initialize()`

```dart
await FirebaseService.initialize();
```

### 2. FCMService
- **Purpose:** Handle all notification-related tasks
- **Features:**
  - Request notifications permissions
  - Handle foreground notifications
  - Handle background notifications
  - Handle terminated state
  - Display local notifications
  - Manage token refresh

```dart
await FCMService().initialize(
  onForegroundMessage: _handleForegroundNotification,
  onBackgroundMessage: _handleBackgroundNotification,
  onMessageOpenedApp: _handleMessageOpenedApp,
);
```

### 3. DeviceTokenService
- **Purpose:** Manage FCM device token storage
- **Features:**
  - Save token to SharedPreferences
  - Retrieve token when needed
  - Clear token on logout
  - Check token staleness

```dart
// Save token
await DeviceTokenService.saveDeviceToken(token);

// Get token
final token = await DeviceTokenService.getDeviceToken();

// Clear on logout
await DeviceTokenService.clearDeviceToken();
```

### 4. Updated AuthRepository
- **Purpose:** Send device token to server
- **Methods Updated:**
  - `login()` - sends deviceToken
  - `register()` - sends deviceToken
  - `logout()` - clears device token
  - `updateDeviceToken()` - updates token if refreshed

```dart
// Login with device token
await repository.login(
  email: email,
  password: password,
  deviceToken: deviceToken, // Automatically retrieved if not provided
);
```

### 5. Updated AuthBloc
- **Purpose:** Coordinate device token with authentication
- **Events Updated:**
  - `AuthLoginRequested` - accepts optional deviceToken
  - `AuthRegisterRequested` - accepts optional deviceToken

```dart
context.read<AuthBloc>().add(
  AuthLoginRequested(
    email: email,
    password: password,
    deviceToken: fcmToken,
  ),
);
```

## GraphQL Mutations

### Updated Login Mutation

The login mutation now includes deviceToken parameter:

```graphql
mutation loginCustomer($input: createCustomerLoginInput!, $deviceToken: String) {
  createCustomerLogin(input: $input) {
    customerLogin {
      id
      apiToken
      token
      message
      success
    }
  }
}
```

### Updated Register Mutation

The register mutation now includes deviceToken parameter:

```graphql
mutation registerCustomer($input: createCustomerInput!, $deviceToken: String) {
  createCustomer(input: $input) {
    customer {
      id
      firstName
      lastName
      email
      phone
      status
      apiToken
      customerGroupId
      subscribedToNewsLetter
      isVerified
      isSuspended
      token
      rememberToken
      name
    }
  }
}
```

### New Update Token Mutation

For refreshing token on server:

```graphql
mutation updateDeviceToken($deviceToken: String!) {
  updateDeviceToken(input: { deviceToken: $deviceToken }) {
    success
    message
  }
}
```

## Notification Handling

### Foreground Notifications (App Open)
When the app is in focus and receives a notification:
1. FCM displays a local notification
2. Custom callback `onForegroundMessage()` is invoked
3. You can handle the notification data and navigate if needed

```dart
Future<void> _handleForegroundNotification(RemoteMessage message) async {
  debugPrint('📬 Foreground notification');
  // TODO: Navigate based on message data
}
```

### Background Notifications (App Minimized)
When the app is in background:
1. FirebaseMessaging displays notification automatically
2. When user taps notification, `onMessageOpenedApp` is called
3. You can navigate to relevant screen

```dart
Future<void> _handleMessageOpenedApp(RemoteMessage message) async {
  debugPrint('📲 App opened from notification');
  // TODO: Navigate based on message data
}
```

### Terminated State (App Killed)
When the app is completely terminated:
1. User taps notification from notification center
2. App launches with initial message
3. `getInitialMessage()` retrieves the message
4. You can navigate appropriately

```dart
final initialMessage = await _messaging.getInitialMessage();
if (initialMessage != null) {
  // Handle the message
}
```

## Integration with Auth Flow

### Login Flow
```
User → Login Page → AuthBloc
  ↓
AuthLoginRequested(email, password)
  ↓
AuthRepository.login()
  ↓
Get deviceToken from DeviceTokenService
  ↓
Send to server with GraphQL mutation
  ↓
Server stores device token
  ↓
AuthAuthenticated state with deviceToken
```

### Register Flow
```
User → Register Page → AuthBloc
  ↓
AuthRegisterRequested(...)
  ↓
AuthRepository.register()
  ↓
Get deviceToken from DeviceTokenService
  ↓
Send to server with GraphQL mutation
  ↓
Server stores device token
  ↓
AuthAuthenticated state with deviceToken
```

### Logout Flow
```
User → Logout → AuthBloc
  ↓
AuthLogoutRequested
  ↓
AuthRepository.logout()
  ↓
Clear device token from DeviceTokenService
  ↓
Clear local auth storage
  ↓
AuthUnauthenticated state
```

### Token Refresh Flow
```
FCM → Token Refresh
  ↓
FCMService._handleTokenRefresh()
  ↓
Save new token to DeviceTokenService
  ↓
If user is authenticated:
  AuthRepository.updateDeviceToken(newToken)
  ↓
Server updates device token
```

## Usage Examples

### Example 1: Basic Login with Notifications

```dart
// In your login page
void _handleLogin() {
  final email = _emailController.text;
  final password = _passwordController.text;
  
  context.read<AuthBloc>().add(
    AuthLoginRequested(
      email: email,
      password: password,
      // deviceToken is optional - automatically retrieved if not provided
    ),
  );
}

// Listen to auth state
BlocListener<AuthBloc, AuthState>(
  listener: (context, state) {
    if (state is AuthAuthenticated) {
      // User logged in, device token is sent to server
      // Notifications can now be received
      Navigator.of(context).pushReplacementNamed('/home');
    } else if (state is AuthError) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(state.message)),
      );
    }
  },
  child: // ... your UI
)
```

### Example 2: Handle Notification Taps

```dart
// In main.dart - update the callback
Future<void> _handleMessageOpenedApp(RemoteMessage message) async {
  debugPrint('📲 App opened from notification');
  
  final data = message.data;
  
  // Navigate based on notification type
  if (data.containsKey('type')) {
    switch (data['type']) {
      case 'order':
        // Navigate to order details
        break;
      case 'promotion':
        // Navigate to promotion
        break;
    }
  }
}
```

### Example 3: Subscribe to Topics (Optional)

```dart
// Subscribe to promotions topic
await FCMService().subscribeToTopic('promotions');

// This allows you to send notifications to all users subscribed to 'promotions'
// from your server/Firebase console
```

## Testing

### Send Test Notification (Firebase Console)

1. Go to Firebase Console → Cloud Messaging
2. Click "New Campaign"
3. Select "Firebase Notifications"
4. Create notification with title and body
5. Click "Send Test Message"
6. Select your test device
7. Check logs for notification handling

### Check Device Token

```bash
# In Flutter app logs, look for:
# 🎫 Device token obtained: ...
```

### Monitor FCM Events

```dart
// Add to main.dart for debugging
FirebaseMessaging.onMessage.listen((message) {
  debugPrint('Foreground: ${message.notification?.title}');
});

FirebaseMessaging.onBackgroundMessage((message) {
  debugPrint('Background: ${message.notification?.title}');
});
```

## Production Checklist

- [ ] Firebase project created and configured
- [ ] Android google-services.json added
- [ ] iOS GoogleService-Info.plist added
- [ ] iOS Push Notifications capability enabled
- [ ] Android Manifest updated with notification permissions
- [ ] firebase_options.dart updated with your config
- [ ] APNs key uploaded to Firebase (iOS)
- [ ] Backend API updated to accept and store deviceToken
- [ ] Tested login/register flow with device token
- [ ] Tested notification receipt and handling
- [ ] Tested logout clears device token
- [ ] Tested foreground, background, and terminated notifications
- [ ] Tested on real iOS device and Android device

## Troubleshooting

### Device Token Not Generated

**Symptoms:** `🎫 Device token obtained: null` in logs

**Solutions:**
1. Ensure Firebase is initialized before FCM
2. Check network connectivity
3. Verify app has internet permission
4. For iOS: run on physical device (simulator has limitations)
5. Clear app data and reinstall

### Notifications Not Received

**Symptoms:** No notifications appear despite being sent

**Solutions:**
1. Verify notification permissions are granted
2. Check app is not in focus (for background notifications)
3. Verify device token is correct on server
4. Check Firebase Console for send errors
5. Ensure notification payload is valid JSON

### Token Refresh Issues

**Symptoms:** Old tokens on server after app restart

**Solutions:**
1. Token refresh happens automatically
2. If user is logged in, call `updateDeviceToken()` to sync
3. Check server is accepting new tokens

### Auth Integration Issues

**Symptoms:** Device token not sent during login

**Solutions:**
1. Ensure FCMService is initialized before auth
2. Check `DeviceTokenService.getDeviceToken()` returns non-null
3. Verify GraphQL mutation includes deviceToken parameter
4. Check server response includes success

## API Reference

### FirebaseService
```dart
static Future<void> initialize()
```

### FCMService
```dart
Future<void> initialize({
  NotificationCallback? onForegroundMessage,
  NotificationCallback? onBackgroundMessage,
  NotificationCallback? onMessageOpenedApp,
})

Future<String?> getDeviceToken()
Future<String?> refreshDeviceToken()
Future<void> setNotificationsEnabled(bool enabled)
Future<void> subscribeToTopic(String topic)
Future<void> unsubscribeFromTopic(String topic)
```

### DeviceTokenService
```dart
static Future<void> saveDeviceToken(String token)
static Future<String?> getDeviceToken()
static Future<void> clearDeviceToken()
static Future<DateTime?> getTokenGeneratedAt()
static Future<bool> isTokenStale()
```

### AuthRepository
```dart
Future<CustomerLogin> login({
  required String email,
  required String password,
  String? deviceToken,
})

Future<Customer> register({
  required String firstName,
  required String lastName,
  required String email,
  required String password,
  required String confirmPassword,
  String? deviceToken,
})

Future<bool> logout()

Future<bool> updateDeviceToken({required String deviceToken})
```

## Next Steps

1. Complete Android setup: [Android_Firebase_Setup.md](Android_Firebase_Setup.md)
2. Complete iOS setup: [iOS_Firebase_Setup.md](iOS_Firebase_Setup.md)
3. Update your backend API to handle deviceToken
4. Implement custom notification handling
5. Test end-to-end flow
6. Set up topic subscriptions if needed
7. Monitor notifications in production
