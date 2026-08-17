import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:graphql_flutter/graphql_flutter.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';
import '../currency/currency_cubit.dart';
import '../constants/api_constants.dart';
import '../locale/locale_cubit.dart';

/// Custom HTTP client wrapper with timeout support
class TimeoutHttpClient extends http.BaseClient {
  final http.Client _inner;
  final Duration connectTimeout;
  final Duration receiveTimeout;

  TimeoutHttpClient({Duration? connectTimeout, Duration? receiveTimeout})
    : _inner = http.Client(),
      connectTimeout = connectTimeout ?? const Duration(seconds: 60),
      receiveTimeout = receiveTimeout ?? const Duration(seconds: 60);

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async {
    final completer = Completer<http.StreamedResponse>();
    final timer = Timer(connectTimeout, () {
      if (!completer.isCompleted) {
        completer.completeError(
          TimeoutException('Request timed out after $connectTimeout'),
        );
      }
    });

    try {
      final response = await _inner.send(request).timeout(receiveTimeout);
      timer.cancel();
      completer.complete(response);
    } catch (e) {
      timer.cancel();
      if (!completer.isCompleted) {
        completer.completeError(e);
      }
    }

    return completer.future;
  }

  @override
  void close() {
    _inner.close();
    super.close();
  }
}

class LoggingHttpClient extends http.BaseClient {
  final http.Client _inner;

  LoggingHttpClient({required http.Client inner}) : _inner = inner;

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async {
    await _attachDynamicHeaders(request);
    final stopwatch = Stopwatch()..start();
    final requestBody = _extractRequestBody(request);
    final requestJson = _tryParseJson(requestBody);
    final operationName = requestJson is Map<String, dynamic>
        ? requestJson['operationName']?.toString()
        : null;
    final variables = requestJson is Map<String, dynamic>
        ? requestJson['variables']
        : null;

    _logApiMessage('═══════════════════════════════════════════');
    _logApiMessage('🌐 [API Request]');
    _logApiMessage('📝 API Name: ${operationName ?? 'unnamed'}');
    _logApiMessage('🔗 URL: ${request.url}');
    _logApiMessage('📨 Request Type: ${request.method}');
    if (requestJson != null || variables != null) {
      _logApiMessage('🧩 Payload attached');
    }

    try {
      final response = await _inner.send(request);
      final responseBytes = await response.stream.toBytes();
      stopwatch.stop();

      final responseBody = utf8.decode(responseBytes, allowMalformed: true);
      _logApiMessage('✅ [API Response] (${stopwatch.elapsedMilliseconds}ms)');
      _logApiMessage('📝 API Name: ${operationName ?? 'unnamed'}');
      _logApiMessage('🔢 Status Code: ${response.statusCode}');
      _logApiMessage(
        '📦 Response Length: ${responseBody.length} chars',
      );
      _logApiMessage('═══════════════════════════════════════════');

      return http.StreamedResponse(
        Stream<List<int>>.value(responseBytes),
        response.statusCode,
        contentLength: response.contentLength,
        request: response.request,
        headers: response.headers,
        isRedirect: response.isRedirect,
        persistentConnection: response.persistentConnection,
        reasonPhrase: response.reasonPhrase,
      );
    } catch (e) {
      stopwatch.stop();
      _logApiMessage('❌ [API Error] (${stopwatch.elapsedMilliseconds}ms)');
      _logApiMessage('📝 API Name: ${operationName ?? 'unnamed'}');
      _logApiMessage('⚠️ Error: $e');
      _logApiMessage('═══════════════════════════════════════════');
      rethrow;
    }
  }

  Future<void> _attachDynamicHeaders(http.BaseRequest request) async {
    final prefs = await SharedPreferences.getInstance();
    final locale = prefs.getString(LocaleCubit.localeKey);
    final currency = prefs.getString(CurrencyCubit.currencyKey);

    request.headers['X-CHANNEL'] = channelCode;
    if (locale != null && locale.isNotEmpty) {
      request.headers['X-LOCALE'] = locale;
    }
    if (currency != null && currency.isNotEmpty) {
      request.headers['X-CURRENCY'] = currency;
    }
  }

  String _extractRequestBody(http.BaseRequest request) {
    if (request is http.Request) {
      return request.body;
    }

    return '';
  }

  dynamic _tryParseJson(String value) {
    if (value.isEmpty) return null;
    try {
      return jsonDecode(value);
    } catch (_) {
      return null;
    }
  }

  @override
  void close() {
    _inner.close();
    super.close();
  }
}

void _logApiMessage(String message) {
  if (!kDebugMode) return;
  debugPrint(message);
}

class GraphQLClientProvider {
  // ─── Constants ────────────────────────────────────────────────────────────

  /// All GraphQL clients use this timeout. Change here once — applies everywhere.
  static const _kTimeout = Duration(seconds: 60);

  // ─── Cache ────────────────────────────────────────────────────────────────

  /// Clears the GraphQL cache (HiveStore).
  /// Call this on logout to remove all cached user data.
  static Future<void> clearCache() async {
    try {
      final store = HiveStore();
      await store.reset();
      debugPrint('✅ GraphQL HiveStore cache cleared');
    } catch (e) {
      debugPrint('⚠️ Failed to clear HiveStore cache: $e');
    }
  }

  // ─── Central factory ─────────────────────────────────────────────────────

  /// **Use this everywhere** instead of constructing [GraphQLClient] manually.
  ///
  /// Automatically applies the 60-second [_kTimeout] so you never have to
  /// repeat it in individual repositories or pages.
  ///
  /// ```dart
  /// // No token (guest / unauthenticated)
  /// GraphQLClientProvider.buildClient()
  ///
  /// // With auth token
  /// GraphQLClientProvider.buildClient(token: accessToken)
  /// ```
  static GraphQLClient buildClient({String? token}) {
    final httpClient = LoggingHttpClient(
      inner: TimeoutHttpClient(
        connectTimeout: _kTimeout,
        receiveTimeout: _kTimeout,
      ),
    );

    final httpLink = HttpLink(
      bagistoEndpoint,
      httpClient: httpClient,
      defaultHeaders: {
        'Content-Type': 'application/json',
        'X-STOREFRONT-KEY': storefrontKey,
      },
    );

    final Link link;
    if (token != null && token.isNotEmpty) {
      final authLink = AuthLink(getToken: () async => 'Bearer $token');
      link = authLink.concat(httpLink);
    } else {
      link = httpLink;
    }

    return GraphQLClient(
      cache: GraphQLCache(store: InMemoryStore()),
      link: link,
      queryRequestTimeout: _kTimeout,
      defaultPolicies: DefaultPolicies(
        query: Policies(fetch: FetchPolicy.noCache),
        mutate: Policies(fetch: FetchPolicy.noCache),
      ),
    );
  }

  // ─── Logging link ─────────────────────────────────────────────────────────

  static Link _createLoggingLink({String label = 'GraphQL'}) {
    return Link.function((request, [forward]) {
      final stopwatch = Stopwatch()..start();
      _logApiMessage('═══════════════════════════════════════════');
      _logApiMessage('🔵 [$label Request]');
      _logApiMessage(
        '📝 Operation: ${request.operation.operationName ?? 'unnamed'}',
      );
      if (request.variables.isNotEmpty) {
        _logApiMessage('🔧 Variables attached');
      }
      _logApiMessage('───────────────────────────────────────────');

      return forward!(request).map((response) {
        stopwatch.stop();
        final duration = stopwatch.elapsedMilliseconds;
        final hasErrors =
            response.errors != null && response.errors!.isNotEmpty;
        if (hasErrors) {
          _logApiMessage('❌ [$label Error Response] (${duration}ms)');
          response.errors?.forEach((error) {
            _logApiMessage('⚠️ Error: ${error.message}');
          });
        } else {
          _logApiMessage('✅ [$label Success Response] (${duration}ms)');
        }
        _logApiMessage('═══════════════════════════════════════════');
        return response;
      });
    });
  }

  // ─── Named clients (for app-level providers) ──────────────────────────────

  /// Unauthenticated client wrapped in [ValueNotifier] for GraphQL widgets.
  static ValueNotifier<GraphQLClient> get client {
    final httpClient = LoggingHttpClient(
      inner: TimeoutHttpClient(
        connectTimeout: _kTimeout,
        receiveTimeout: _kTimeout,
      ),
    );

    final httpLink = HttpLink(
      bagistoEndpoint,
      httpClient: httpClient,
      defaultHeaders: {
        'Content-Type': 'application/json',
        'X-STOREFRONT-KEY': storefrontKey,
      },
    );

    final link = _createLoggingLink().concat(httpLink);

    Store store;
    try {
      store = HiveStore();
    } catch (_) {
      store = InMemoryStore();
    }

    return ValueNotifier(
      GraphQLClient(
        cache: GraphQLCache(store: store),
        link: link,
        queryRequestTimeout: _kTimeout,
        defaultPolicies: DefaultPolicies(
          query: Policies(fetch: FetchPolicy.networkOnly),
        ),
      ),
    );
  }

  /// Authenticated client wrapped in [ValueNotifier] for GraphQL widgets.
  static ValueNotifier<GraphQLClient> authenticatedClient(String accessToken) {
    final httpClient = LoggingHttpClient(
      inner: TimeoutHttpClient(
        connectTimeout: _kTimeout,
        receiveTimeout: _kTimeout,
      ),
    );

    final httpLink = HttpLink(
      bagistoEndpoint,
      httpClient: httpClient,
      defaultHeaders: {
        'Content-Type': 'application/json',
        'X-STOREFRONT-KEY': storefrontKey,
      },
    );

    final authLink = AuthLink(getToken: () async => 'Bearer $accessToken');
    final link = authLink.concat(
      _createLoggingLink(label: 'GraphQL Auth').concat(httpLink),
    );

    Store store;
    try {
      store = HiveStore();
    } catch (_) {
      store = InMemoryStore();
    }

    return ValueNotifier(
      GraphQLClient(
        cache: GraphQLCache(store: store),
        link: link,
        queryRequestTimeout: _kTimeout,
        defaultPolicies: DefaultPolicies(
          query: Policies(fetch: FetchPolicy.networkOnly),
        ),
      ),
    );
  }
}
