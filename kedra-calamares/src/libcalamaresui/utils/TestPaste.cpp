/* === This file is part of Calamares - <https://calamares.io> ===
 *
 *   SPDX-FileCopyrightText: 2021 Adriaan de Groot <groot@kde.org>
 *   SPDX-License-Identifier: GPL-3.0-or-later
 *
 *
 *   Calamares is Free Software: see the License-Identifier above.
 *
 *
 */

#include "Paste.h"

#include "utils/Logger.h"

#include <QNetworkReply>
#include <QTimer>
#include <QtTest/QtTest>

extern QByteArray logFileContents( qint64 sizeLimitBytes );
extern QString httpPasteUrl( const QByteArray& responseText, const QUrl& serverUrl );
extern bool waitForReply( QNetworkReply* reply, int timeoutMs );

class FakeNetworkReply : public QNetworkReply
{
public:
    explicit FakeNetworkReply( QObject* parent = nullptr )
        : QNetworkReply( parent )
    {
        open( QIODevice::ReadOnly | QIODevice::Unbuffered );
        setOperation( QNetworkAccessManager::PostOperation );
        setUrl( QUrl( "https://paste.cachyos.org" ) );
    }

    void succeed( const QByteArray& data = QByteArray() )
    {
        m_data = data;
        m_offset = 0;
        setFinished( true );
        emit readyRead();
        emit finished();
    }

    bool aborted() const { return m_aborted; }

    void abort() override
    {
        m_aborted = true;
        setFinished( true );
        setError( QNetworkReply::OperationCanceledError, "timed out" );
        emit finished();
    }

    qint64 bytesAvailable() const override
    {
        return m_data.size() - m_offset + QIODevice::bytesAvailable();
    }

protected:
    qint64 readData( char* data, qint64 maxlen ) override
    {
        if ( m_offset >= m_data.size() )
        {
            return -1;
        }

        const qint64 len = qMin< qint64 >( maxlen, m_data.size() - m_offset );
        memcpy( data, m_data.constData() + m_offset, size_t( len ) );
        m_offset += len;
        return len;
    }

private:
    QByteArray m_data;
    qint64 m_offset = 0;
    bool m_aborted = false;
};

class TestPaste : public QObject
{
    Q_OBJECT

public:
    TestPaste() {}
    ~TestPaste() override {}

private Q_SLOTS:
    void testGetLogFile();
    void testHttpPasteUrl();
    void testWaitForReplyCompletes();
    void testWaitForReplyTimeout();
};

void
TestPaste::testGetLogFile()
{
    QFile::remove( Logger::logFile() );
    // This test assumes nothing **else** has set up logging yet
    QByteArray logLimitedBefore = logFileContents( 16 );
    QVERIFY( logLimitedBefore.isEmpty() );
    QByteArray logUnlimitedBefore = logFileContents( -1 );
    QVERIFY( logUnlimitedBefore.isEmpty() );

    Logger::setupLogLevel( Logger::LOGDEBUG );
    Logger::setupLogfile();

    QByteArray logLimitedAfter = logFileContents( 16 );
    QVERIFY( !logLimitedAfter.isEmpty() );
    QByteArray logUnlimitedAfter = logFileContents( -1 );
    QVERIFY( !logUnlimitedAfter.isEmpty() );
}

void
TestPaste::testHttpPasteUrl()
{
    const QUrl server( "https://paste.cachyos.org" );

    // Relative path response
    QCOMPARE( httpPasteUrl( QByteArray( "/abc123\n" ), server ),
              QString( "https://paste.cachyos.org/abc123.log" ) );
    // Multi-segment path as returned by paste.cachyos.org
    QCOMPARE( httpPasteUrl( QByteArray( "/p/ca17c60\n" ), server ),
              QString( "https://paste.cachyos.org/p/ca17c60.log" ) );
    // Absolute URL on the same origin
    QCOMPARE( httpPasteUrl( QByteArray( "https://paste.cachyos.org/abc123" ), server ),
              QString( "https://paste.cachyos.org/abc123.log" ) );

    // Rejected: cross-origin absolute URL
    QVERIFY( httpPasteUrl( QByteArray( "https://example.org/abc123\n" ), server ).isEmpty() );
    // Rejected: empty response
    QVERIFY( httpPasteUrl( QByteArray( "" ), server ).isEmpty() );
    // Rejected: HTML error page (Cloudflare interstitial, etc.)
    QVERIFY( httpPasteUrl( QByteArray( "<html><body>503</body></html>" ), server ).isEmpty() );
    // Rejected: response with a query string
    QVERIFY( httpPasteUrl( QByteArray( "/abc123?foo=bar" ), server ).isEmpty() );
    // Rejected: response with a fragment
    QVERIFY( httpPasteUrl( QByteArray( "/abc123#frag" ), server ).isEmpty() );
    // Rejected: path traversal
    QVERIFY( httpPasteUrl( QByteArray( "/../etc/passwd" ), server ).isEmpty() );
    // Rejected: "." / ".." segments mid-path
    QVERIFY( httpPasteUrl( QByteArray( "/p/../etc/passwd" ), server ).isEmpty() );
    QVERIFY( httpPasteUrl( QByteArray( "/p/./abc" ), server ).isEmpty() );
}

void
TestPaste::testWaitForReplyCompletes()
{
    FakeNetworkReply reply;
    QTimer::singleShot( 0, &reply, [ &reply ]() { reply.succeed( QByteArray( "/abc123" ) ); } );

    QVERIFY( waitForReply( &reply, 100 ) );
    QVERIFY( !reply.aborted() );
}

void
TestPaste::testWaitForReplyTimeout()
{
    FakeNetworkReply reply;

    QVERIFY( !waitForReply( &reply, 10 ) );
    QVERIFY( reply.aborted() );
}

QTEST_GUILESS_MAIN( TestPaste )

#include "utils/moc-warnings.h"

#include "TestPaste.moc"
