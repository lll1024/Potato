import FactoryKit
import Foundation
import SessionKit

extension Container {
    @MainActor
    var sessions: Factory<Result<any SessionAccess, Error>> {
        self {
            Result {
                let directory = URL.applicationSupportDirectory.appending(path: "Potato", directoryHint: .isDirectory)
                return try SharedSessions(databaseURL: directory.appending(path: "client.sqlite")) as any SessionAccess
            }
        }.singleton
    }
}
