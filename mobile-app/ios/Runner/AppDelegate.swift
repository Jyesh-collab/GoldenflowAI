import Flutter
import UIKit
import FirebaseCore
import FirebaseMessaging

@main
@objc class AppDelegate: FlutterAppDelegate {
  override func application(
    _ application: UIApplication,
    didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?
  ) -> Bool {
    let firebaseEnabled = isFirebaseConfigAvailable()

    // Register for remote notifications (required for Firebase Cloud Messaging)
    if firebaseEnabled {
      if #available(iOS 10.0, *) {
        UNUserNotificationCenter.current().delegate = self
        let authOptions: UNAuthorizationOptions = [.alert, .badge, .sound]
        UNUserNotificationCenter.current().requestAuthorization(
          options: authOptions,
          completionHandler: { _, _ in }
        )
      } else {
        let settings: UIUserNotificationSettings =
          UIUserNotificationSettings(types: [.alert, .badge, .sound], categories: nil)
        application.registerUserNotificationSettings(settings)
      }
      application.registerForRemoteNotifications()
    } else {
      print("Firebase config is placeholder-only. Skipping native notification registration.")
    }

    GeneratedPluginRegistrant.register(with: self)

    // Pre-warm the iOS keyboard to avoid ~1s jank on first TextField focus.
    // Creates a native UITextField, makes it first responder (triggers keyboard
    // framework loading), then immediately resigns and removes it.
    DispatchQueue.main.async {
      let warmupField = UITextField(frame: .zero)
      warmupField.autocorrectionType = .no
      self.window?.addSubview(warmupField)
      warmupField.becomeFirstResponder()
      warmupField.resignFirstResponder()
      warmupField.removeFromSuperview()
    }

    return super.application(application, didFinishLaunchingWithOptions: launchOptions)
  }

  // MARK: - Push Notification Handling

  override func application(
    _ application: UIApplication,
    didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data
  ) {
    // Pass device token to Firebase Messaging
    if FirebaseApp.app() != nil {
      Messaging.messaging().apnsToken = deviceToken
    } else {
      print("Firebase not configured. Ignoring APNS token assignment.")
    }
    super.application(application, didRegisterForRemoteNotificationsWithDeviceToken: deviceToken)
  }

  override func application(
    _ application: UIApplication,
    didFailToRegisterForRemoteNotificationsWithError error: Error
  ) {
    print("Failed to register for remote notifications: \(error.localizedDescription)")
  }

  private func isFirebaseConfigAvailable() -> Bool {
    guard
      let path = Bundle.main.path(forResource: "GoogleService-Info", ofType: "plist"),
      let config = NSDictionary(contentsOfFile: path),
      let projectId = config["PROJECT_ID"] as? String
    else {
      return false
    }

    return projectId != "placeholder-firebase-project"
  }
}
