import 'package:bagisto_flutter/features/category/data/models/product_model.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('BookingEventTicket', () {
    test(
      'reads name and description from translation when top-level fields are absent',
      () {
        final ticket = BookingEventTicket.fromJson({
          'id': '2',
          '_id': 2,
          'bookingProductId': 11,
          'price': 125,
          'formattedPrice': r'$125.00',
          'qty': 100,
          'specialPrice': 120,
          'formattedSpecialPrice': r'$120.00',
          'translation': {
            'locale': 'en',
            'name': 'VIP Access Ticket',
            'description':
                'VIP ticket includes priority entry, reserved seating, and access to exclusive areas near the stage for a premium experience.',
          },
        });

        expect(ticket.id, '2');
        expect(ticket.bookingProductId, 11);
        expect(ticket.name, 'VIP Access Ticket');
        expect(
          ticket.description,
          'VIP ticket includes priority entry, reserved seating, and access to exclusive areas near the stage for a premium experience.',
        );
        expect(ticket.displayPriceLabel, r'$120.00');
        expect(ticket.originalPriceLabel, r'$125.00');
      },
    );
  });
}
