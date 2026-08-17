# Firebase Push Notifications - iOS Setup Guide

## Step 1: Create Firebase Project (if not already done)

1. Go to [Firebase Console](https://console.firebase.google.com)
2. Create a new project or use an existing one
3. Make note of your project ID and project number

## Step 2: Create Apple App in Firebase

1. In Firebase Console, click "Add app" → "iOS"
2. Enter your iOS Bundle ID: `com.example.bagistoFlutter`
3. (Optional) Enter other details like App Store ID
4. Download `GoogleService-Info.plist`

## Step 3: Place GoogleService-Info.plist

1. Open your iOS project in Xcode:
   ```bash
   open ios/Runner.xcworkspace
   ```

2. Drag and drop `GoogleService-Info.plist` into Xcode
   - Select "Copy items if needed"
   - Make sure it's added to all targets (Runner, RunnerTests)

3. Verify the file is in the correct location:
   ```
   ios/Runner/GoogleService-Info.plist
   ```

## Step 4: Configure iOS in Xcode

### Step 4.1: Enable Push Notifications

1. Select `Runner` project in Xcode
2. Select `Runner` target
3. Go to "Signing & Capabilities"
4. Click "+ Capability"
5. Search for and add "Push Notifications"

### Step 4.2: Add APNs Key (Optional but Recommended)

1. Go to [Apple Developer Portal](https://developer.apple.com)
2. Go to Certificates, Identifiers & Profiles
3. Create a new "Apple Push Notification service (APNs) key"
4. Download the key (`.p8` file)
5. In Firebase Console → Project Settings → Cloud Messaging → iOS app settings
6. Upload the APNs key
7. Enter your Key ID and Team ID

### Step 4.3: Update Podfile

The Podfile should already have Firebase pods configured. Verify it contains:

```ruby
# File: ios/Podfile

target 'Runner' do
  flutter_root = File.expand_path(File.join(packages_dir, 'flutter'))
  load File.join(flutter_root, 'packages', 'flutter_tools', 'bin', 'podhelper')

  flutter_ios_podfile_setup

  post_install do |installer|
    installer.pods_project.targets.each do |target|
      flutter_install_all_ios_build_settings installer
    end
  end
end
```

No additional changes needed - Flutter handles Firebase pods automatically.

### Step 4.4: Update Info.plist

Edit `ios/Runner/Info.plist` and add:

```xml
<key>FirebaseAppDelegateProxyEnabled</key>
<false/>
```

This prevents Firebase from swizzling your app delegate (since Flutter handles this).

## Step 5: Build and Test

1. Clean the project:
   ```bash
   flutter clean
   rm -rf ios/Pods ios/Podfile.lock
   ```

2. Get dependencies:
   ```bash
   flutter pub get
   ```

3. Build for iOS:
   ```bash
   flutter run -d <device_id>
   ```

4. Check the device token in logs:
   ```
   🎫 Device token obtained: ...
   ```

## Step 6: Test iOS Push Notifications

### Using Firebase Console

1. Go to Cloud Messaging section in Firebase
2. Click "New campaign" → "Firebase Notifications"
3. Create a notification
4. Select your iOS app
5. Click "Send test message"
6. Select device(s) and send

### Using Apple Terminal (Advanced)

You can also use `curl` with an APNs certificate to send test notifications.

## Step 7: iOS App Signing (Production)

### Automatic Signing (Recommended)

1. In Xcode, select Runner → Signing & Capabilities
2. Check "Automatically manage signing"
3. Select your Team

### Manual Signing (Advanced)

1. Create certificates in Apple Developer Portal
2. Update provisioning profiles
3. Configure in Xcode accordingly

## Step 8: Permissions

iOS 10+ requires explicit permission for notifications. The code already handles this:

```dart
final settings = await _messaging.requestPermission();
```

Users will be prompted when the app first tries to display notifications.

## Troubleshooting

### Issue: "GoogleService-Info.plist not found"
- **Solution:** 
  - Verify file is in `ios/Runner/` directory
  - Check it's added to all targets in Xcode
  - Clean and rebuild: `flutter clean && flutter run`

### Issue: APNs key not configured
- **Solution:**
  - While optional, it's highly recommended
  - Upload APNs key in Firebase Console → Project Settings
  - Without it, notifications may not be received in production

### Issue: Push Notifications disabled
- **Solution:**
  - Check "Push Notifications" capability is enabled
  - Run on a physical device (simulators have limited FCM support)
  - Check device Settings → Bagisto Store → Notifications is enabled

### Issue: Notifications not appearing
- **Solution:**
  - Ensure your app is installed on physical device
  - Check foreground notification display settings
  - Verify notification permissions are granted in Settings

### Issue: "Method not implemented" in simulator
- **Solution:** 
  - FCM has limited simulator support
  - Test on physical iOS device for full functionality

## Common Issues with Solutions

| Issue | Solution |
|-------|----------|
| Pod install fails | Run `pod repo update` and try again |
| Compilation errors | Ensure GoogleService-Info.plist is added to all targets |
| Token not generated | Ensure running on physical device with network access |
| Notifications in background | Verify notification permission is granted |
| App crashes on startup | Check Podfile and Info.plist configuration |

## After Setup

1. Update `firebase_options.dart` with your Firebase configuration
2. Test on physical iOS device
3. Implement custom notification handling in your app
4. Set up Android notifications (see Android_Firebase_Setup.md)
5. Test end-to-end flow: login → receive notification → logout

## References

- [Firebase iOS Setup](https://firebase.google.com/docs/cloud-messaging/ios/client)
- [Flutter Firebase Plugin](https://pub.dev/packages/firebase_messaging)
- [Apple Push Notifications](https://developer.apple.com/notification/)
