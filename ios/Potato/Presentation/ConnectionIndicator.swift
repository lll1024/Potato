import SessionKit
import SwiftUI

struct ConnectionIndicator: View {
    let state: ConnectionState

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            switch state {
            case .unconfigured:
                Label("尚未配置", systemImage: "network")
            case .connecting:
                HStack { ProgressView(); Text("连接中") }
            case .connected:
                Label("已连接", systemImage: "checkmark.circle")
                    .foregroundStyle(.green)
            case .unavailable(let message):
                Label("连接不可用", systemImage: "wifi.exclamationmark")
                    .foregroundStyle(.red)
                Text(message).font(.subheadline).foregroundStyle(.secondary)
            }
        }
        .accessibilityElement(children: .contain)
    }
}
