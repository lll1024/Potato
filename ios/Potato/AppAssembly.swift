import FactoryKit
import Foundation
import SessionKit

extension Container {
    @MainActor
    var sessions: Factory<Result<any SessionAccess, Error>> {
        self {
            Result {
                let directory = URL.applicationSupportDirectory.appending(path: "Potato", directoryHint: .isDirectory)
                let sessions = try SharedSessions(databaseURL: directory.appending(path: "client.sqlite"))
                #if DEBUG && targetEnvironment(simulator)
                if sessions.serverAddress.isEmpty {
                    try sessions.configure(address: "http://127.0.0.1:8000")
                }
                #endif
                return sessions as any SessionAccess
            }
        }.singleton
    }
}
