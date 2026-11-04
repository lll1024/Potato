import SessionKit
import SwiftUI

struct ConnectionSettingsView: View {
    let sessions: any SessionAccess
    @Environment(\.dismiss) private var dismiss
    @State private var address: String
    @State private var error: String?
    @State private var saving = false
    @FocusState private var editing: Bool

    init(sessions: any SessionAccess) {
        self.sessions = sessions
        _address = State(initialValue: sessions.serverAddress)
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("服务地址") {
                    TextField("https://你的电脑.ts.net", text: $address)
                        .textContentType(.URL).keyboardType(.URL)
                        .textInputAutocapitalization(.never).autocorrectionDisabled()
                        .focused($editing).submitLabel(.done).onSubmit { editing = false }
                        .accessibilityLabel("HTTPS 服务地址").accessibilityIdentifier("server-address")
                    Button("保存并连接") { Task { await connect() } }
                        .disabled(saving || address.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                        .accessibilityIdentifier("save-connection")
                }
                Section {
                    ConnectionIndicator(state: sessions.connection)
                    if let error { Text(error).foregroundStyle(.red) }
                }
                Section {
                    Text("让 iPhone 与 Mac 加入同一 Tailscale 私人网络，并填写 Mac 的 HTTPS 服务地址。Mac 和旅行助手需要保持运行。")
                        .foregroundStyle(.secondary)
                }
                if !sessions.serverAddress.isEmpty {
                    Section {
                        Button("清除已保存地址", role: .destructive) {
                            do {
                                try sessions.configure(address: "")
                                address = ""
                                error = nil
                            } catch { self.error = "配置未保存，请检查本机存储后重试。" }
                        }
                        .disabled(saving)
                        .accessibilityIdentifier("clear-connection")
                    }
                }
            }
            .navigationTitle("连接配置")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("完成") { dismiss() }.accessibilityIdentifier("done-connection")
                }
            }
        }
    }

    private func connect() async {
        editing = false
        saving = true
        error = nil
        defer { saving = false }
        do {
            try sessions.configure(address: address)
            address = sessions.serverAddress
        } catch let failure as SessionFailure {
            error = failure.localizedDescription
            return
        } catch {
            self.error = "配置未保存，请检查本机存储后重试。"
            return
        }
        do { _ = try await sessions.refresh() }
        catch { /* 连接错误由共享连接状态展示。 */ }
    }
}
