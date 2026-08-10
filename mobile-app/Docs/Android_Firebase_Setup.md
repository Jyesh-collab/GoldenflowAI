# Firebase Push Notifications - Android Setup Guide

## Step 1: Create Firebase Project

1. Go to [Firebase Console](https://console.firebase.google.com)
2. Click "Create a new project" or select an existing project
3. Follow the setup wizard to create/select your project

## Step 2: Add Android App to Firebase

1. In Firebase Console, click "Add app" → "Android"
2. Enter your package name: `com.example.bagisto_flutter`
3. (Optional) Enter SHA-1 fingerprint for Google Sign-In:
   ```bash
   # Generate SHA-1 from your signing key:
   keytool -list -v -keystore ~/.android/debug.keystore -alias androiddebugkey -storepass android -keypass android
   ```
4. Download `google-services.json`

## Step 3: Place google-services.json

1. Copy the downloaded `google-services.json` file to:
   ```
   android/app/google-services.json
   ```

## Step 4: Update Android Build Files

### android/build.gradle.kts

Add Google Services plugin dependency:

```kotlin
plugins {
    id("com.google.gms.google-services") version "4.4.1" apply false
}

buildscript {
    dependencies {
        classpath("com.google.gms:google-services:4.4.1")
    }
}
```

### android/app/build.gradle.kts

Add the Google Services plugin and Firebase dependencies:

```kotlin
plugins {
    id("com.android.application")
    id("kotlin-android")
    id("com.google.gms.google-services")
}

dependencies {
    // Firebase
    implementation(platform("com.google.firebase:firebase-bom:33.0.0"))
    implementation("com.google.firebase:firebase-messaging")
    
    // Other existing dependencies...
}
```

## Step 5: Configure Android Manifest

Update `android/app/src/main/AndroidManifest.xml`:

```xml
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android">
    <!-- Existing permissions -->
    <uses-permission android:name="android.permission.INTERNET" />
    <uses-permission android:name="android.permission.ACCESS_NETWORK_STATE" />
    
    <application>
        <!-- Existing activities -->
        
        <!-- Firebase Notification Service -->
        <service
            android:name=".MainActivity"
            android:exported="false">
            <intent-filter>
                <action android:name="com.google.firebase.MESSAGING_EVENT" />
            </intent-filter>
        </service>
        
        <!-- Default notification channel -->
        <meta-data
            android:name="com.google.firebase.messaging.default_notification_channel_id"
            android:value="bagisto_notifications" />
    </application>
</manifest>
```

## Step 6: Handle Messages in Android

The FCM service is already configured in `lib/core/notifications/fcm_service.dart`.

Firebase Cloud Messaging will be automatically initialized when the app starts.

## Step 7: Test Android Push Notifications

1. Build and run your app:
   ```bash
   flutter run -d <device_id>
   ```

2. Check the device token in the logs:
   ```
   🎫 Device token obtained: ...
   ```

3. Send a test notification from Firebase Console:
   - Go to Cloud Messaging section
   - Click "Send your first message"
   - Create a notification
   - Select "Send to a topic" or specific device using token
   - Click "Send"

## Step 8: Android App Signing (Production)

For release builds, create a signing key:

```bash
# Navigate to android directory
cd android

# Create a key
keytool -genkey -v -keystore app-release-key.keystore -keyalg RSA -keysize 2048 -validity 10000 -alias app

# Fill in the prompts (passwords, names, etc.)
```

Then configure in `android/app/build.gradle.kts`:

```kotlin
signingConfigs {
    create("release") {
        storeFile = file("app-release-key.keystore")
        storePassword = "your_keystore_password"
        keyAlias = "app"
        keyPassword = "your_key_password"
    }
}

buildTypes {
    release {
        signingConfig = signingConfigs.getByName("release")
    }
}
```

## Troubleshooting

### Issue: "google-services.json not found"
- **Solution:** Ensure the file is placed in `android/app/` directory
- Clear build cache: `flutter clean && android/gradlew clean`

### Issue: FCM token not generated
- **Solution:** Ensure Google Play Services are installed on device/emulator
- Go to Settings → Apps → Google Play Services → check if installed/enabled

### Issue: Notifications not received
- **Solution:** 
  - Check notification permissions are granted
  - Verify app has internet access
  - Check logcat for errors: `adb logcat | grep -i firebase`

### Issue: Topic subscription fails
- **Solution:** Ensure network is available before subscribing to topics

## Next Steps

1. Update `firebase_options.dart` with your Firebase configuration
2. Test on Android device or emulator
3. Set up iOS notifications (see iOS_SETUP.md)
4. Implement custom notification handling in your app
