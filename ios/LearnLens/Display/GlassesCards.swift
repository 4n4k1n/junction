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
  /// One instruction card: a small caption, the instruction, and up to two buttons.
  /// Each send replaces the previous card and its button handlers.
  static func card(
    caption: String,
    text: String,
    primary: CardAction? = nil,
    secondary: CardAction? = nil
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
          }
        }
      }
    }
  }
}

/// Lets files that also import SwiftUI name the card type without importing MWDATDisplay.
typealias FlexBoxCard = FlexBox
