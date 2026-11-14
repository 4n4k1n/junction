//
// GlassesCards.swift
//
// What LearnLens draws on the glasses. Imports MWDATDisplay without SwiftUI on purpose:
// Text, Button and Image exist in both and would be ambiguous.
//

import MWDATDisplay

struct CardAction: Sendable {
  let label: String
  var icon: IconName? = nil
  let handler: @Sendable () -> Void
}

enum GlassesCards {
  /// One instruction card: a small caption, the instruction, and up to three buttons in the order
  /// secondary, primary, tertiary. The primary one has the focus when the card appears.
  /// Each send replaces the previous card and its button handlers.
  static func card(
    caption: String,
    text: String,
    primary: CardAction? = nil,
    secondary: CardAction? = nil,
    tertiary: CardAction? = nil
  ) -> FlexBox {
    FlexBox(direction: .column, spacing: 12) {
      FlexBox(direction: .column, spacing: 4) {
        Text(caption, style: .meta, color: .secondary)
        Text(text, style: .body)
      }
      .padding(24)
      .background(.card)

      if primary != nil || secondary != nil {
        FlexBox(direction: .row, spacing: 8, alignment: .center, crossAlignment: .center) {
          ButtonGroup {
            if let secondary {
              Button(
                label: secondary.label,
                style: .secondary,
                iconName: secondary.icon,
                onClick: secondary.handler
              )
            }
            if let primary {
              Button(
                label: primary.label,
                style: .primary,
                iconName: primary.icon,
                onClick: primary.handler
              )
              .actionRole(.primary)
            }
            if let tertiary {
              Button(
                label: tertiary.label,
                style: .secondary,
                iconName: tertiary.icon,
                onClick: tertiary.handler
              )
            }
          }
        }
      }
    }
  }
}

extension GlassesCards {
  /// A question with a few tappable answers, each on its own row, and up to two buttons below.
  static func choices(
    caption: String,
    text: String,
    options: [CardAction],
    primary: CardAction? = nil,
    secondary: CardAction? = nil
  ) -> FlexBox {
    FlexBox(direction: .column, spacing: 8) {
      FlexBox(direction: .column, spacing: 4) {
        Text(caption, style: .meta, color: .secondary)
        Text(text, style: .body)
      }
      .padding(16)

      for option in options {
        FlexBox(direction: .column) {
          Text(option.label, style: .body)
        }
        .padding(16)
        .background(.card)
        .onTap(option.handler)
      }

      if primary != nil || secondary != nil {
        FlexBox(direction: .row, spacing: 8, alignment: .center, crossAlignment: .center) {
          ButtonGroup {
            if let secondary {
              Button(label: secondary.label, style: .secondary, iconName: secondary.icon, onClick: secondary.handler)
            }
            if let primary {
              Button(label: primary.label, style: .primary, iconName: primary.icon, onClick: primary.handler)
            }
          }
        }
      }
    }
  }
}

/// Lets files that also import SwiftUI name the card type without importing MWDATDisplay.
typealias FlexBoxCard = FlexBox
