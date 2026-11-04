import Alamofire
import Foundation
@testable import SessionKit

/// 故障替身只位于 HTTP 传输边界，测试断言经 SessionAccess 进入。
actor HTTPFixture {
    private var requests: [ControlledRequest] = []
    private var waiters: [CheckedContinuation<ControlledRequest, Never>] = []

    func receive(_ request: ControlledRequest) {
        if waiters.isEmpty { requests.append(request) }
        else { waiters.removeFirst().resume(returning: request) }
    }

    func next() async -> ControlledRequest {
        if !requests.isEmpty { return requests.removeFirst() }
        return await withCheckedContinuation { waiters.append($0) }
    }

    @MainActor static func client(databaseURL: URL) async throws -> (any SessionAccess, HTTPFixture, String) {
        let fixture = HTTPFixture()
        let host = UUID().uuidString.lowercased() + ".example.test"
        await HTTPRegistry.shared.register(host: host, fixture: fixture)
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [ControlledURLProtocol.self]
        let sessions = try SharedSessions(databaseURL: databaseURL,
                                          http: SessionHTTP(session: Session(configuration: configuration)))
        let address = "https://" + host
        try sessions.configure(address: address)
        return (sessions, fixture, address)
    }
}

private actor HTTPRegistry {
    static let shared = HTTPRegistry()
    private var fixtures: [String: HTTPFixture] = [:]
    func register(host: String, fixture: HTTPFixture) { fixtures[host] = fixture }
    func fixture(host: String) -> HTTPFixture? { fixtures[host] }
}

final class ControlledURLProtocol: URLProtocol, @unchecked Sendable {
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        let host = request.url!.host!
        let pending = ControlledRequest(protocolRequest: self)
        Task {
            if let fixture = await HTTPRegistry.shared.fixture(host: host) {
                await fixture.receive(pending)
            } else {
                pending.fail()
            }
        }
    }
    override func stopLoading() { }
}

/// URLProtocol 的响应按 fixture 的顺序只完成一次，不共享可变测试状态。
struct ControlledRequest: @unchecked Sendable {
    let protocolRequest: URLProtocol
    func fail() {
        protocolRequest.client?.urlProtocol(protocolRequest, didFailWithError: URLError(.cannotConnectToHost))
    }
    func reply(status: Int = 200, body: String) {
        let response = HTTPURLResponse(url: protocolRequest.request.url!, statusCode: status, httpVersion: "HTTP/1.1",
                                       headerFields: ["Content-Type": "application/json"])!
        protocolRequest.client?.urlProtocol(protocolRequest, didReceive: response, cacheStoragePolicy: .notAllowed)
        protocolRequest.client?.urlProtocol(protocolRequest, didLoad: Data(body.utf8))
        protocolRequest.client?.urlProtocolDidFinishLoading(protocolRequest)
    }
}
