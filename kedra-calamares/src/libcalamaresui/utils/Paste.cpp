/* === This file is part of Calamares - <https://calamares.io> ===
 *
 *   SPDX-FileCopyrightText: 2019 Bill Auger
 *   SPDX-License-Identifier: GPL-3.0-or-later
 *
 *   Calamares is Free Software: see the License-Identifier above.
 *
 */

#include "Paste.h"

#include "Branding.h"
#include "DllMacro.h"
#include "utils/Logger.h"
#include "utils/Units.h"
#include "widgets/TranslationFix.h"

#include <QApplication>
#include <QClipboard>
#include <QEventLoop>
#include <QFile>
#include <QFileInfo>
#include <QMessageBox>
#include <QNetworkAccessManager>
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QRegularExpression>
#include <QTcpSocket>
#include <QTimer>
#include <QUrl>
#include <QWidget>

using namespace Calamares::Units;

/** @brief Reads the logfile, returns its contents.
 *
 * Returns an empty QByteArray() on any kind of error.
 */
STATICTEST QByteArray
logFileContents( const qint64 sizeLimitBytes )
{
    if ( sizeLimitBytes > 0 )
    {
        cDebug() << "Log upload size limit was limited to" << sizeLimitBytes << "bytes";
    }
    if ( sizeLimitBytes == 0 )
    {
        cDebug() << "Log upload size is 0, upload disabled.";
        return QByteArray();
    }

    const QString name = Logger::logFile();
    QFile pasteSourceFile( name );
    if ( !pasteSourceFile.open( QIODevice::ReadOnly | QIODevice::Text ) )
    {
        cWarning() << "Could not open log file" << name;
        return QByteArray();
    }
    if ( sizeLimitBytes < 0 )
    {
        return pasteSourceFile.readAll();
    }
    QFileInfo fi( pasteSourceFile );
    if ( fi.size() > sizeLimitBytes )
    {
        cDebug() << "Only last" << sizeLimitBytes << "bytes of log file (sized" << fi.size() << "bytes) uploaded";
        fi.refresh();  // Because we just wrote to the file with that cDebug() ^^
        pasteSourceFile.seek( fi.size() - sizeLimitBytes );
    }
    return pasteSourceFile.read( sizeLimitBytes );
}

STATICTEST QString
ficheLogUpload( const QByteArray& pasteData, const QUrl& serverUrl, QObject* parent )
{
    QTcpSocket* socket = new QTcpSocket( parent );
    // 16 bits of port-number
    socket->connectToHost( serverUrl.host(), quint16( serverUrl.port() ) );

    if ( !socket->waitForConnected() )
    {
        cError() << "Could not connect to paste server";
        socket->close();
        return QString();
    }

    cDebug() << "Connected to paste server" << serverUrl.host();

    socket->write( pasteData );

    if ( !socket->waitForBytesWritten() )
    {
        cError() << "Could not write to paste server";
        socket->close();
        return QString();
    }

    cDebug() << Logger::SubEntry << "Paste data written to paste server";

    if ( !socket->waitForReadyRead() )
    {
        cError() << "No data from paste server";
        socket->close();
        return QString();
    }

    cDebug() << Logger::SubEntry << "Reading response from paste server";
    QByteArray responseText = socket->readLine( 1024 );
    socket->close();

    QUrl pasteUrl = QUrl( QString( responseText ).trimmed(), QUrl::StrictMode );
    if ( pasteUrl.isValid() && pasteUrl.host() == serverUrl.host() )
    {
        cDebug() << Logger::SubEntry << "Paste server results:" << pasteUrl;
        return pasteUrl.toString();
    }
    else
    {
        cError() << "No data from paste server";
        return QString();
    }
}

STATICTEST QString
httpPasteUrl( const QByteArray& responseText, const QUrl& serverUrl )
{
    if ( !serverUrl.isValid() )
    {
        cError() << "Paste server URL is invalid";
        return QString();
    }

    const QString responsePath = QString::fromUtf8( responseText ).trimmed();
    if ( responsePath.isEmpty() )
    {
        cError() << "No data from paste server";
        return QString();
    }

    const QUrl responseUrl( responsePath, QUrl::StrictMode );
    if ( !responseUrl.isValid() )
    {
        cError() << "Paste server returned an invalid URL";
        return QString();
    }

    // Validate the raw response path *before* resolving, so "." / ".." segments
    // can't be normalized away. Expect an identifier path like "/abc123" or
    // "/p/abc123" and reject HTML error pages, traversal, queries, fragments.
    static const QRegularExpression pathPattern(
        QStringLiteral( "\\A(/[A-Za-z0-9_~][A-Za-z0-9._~-]*)+\\z" ) );
    if ( !pathPattern.match( responseUrl.path() ).hasMatch() || responseUrl.hasQuery()
         || responseUrl.hasFragment() )
    {
        cError() << "Paste server returned an unexpected URL";
        return QString();
    }

    QUrl pasteUrl = serverUrl.resolved( responseUrl );
    if ( !pasteUrl.isValid() || pasteUrl.host() != serverUrl.host() || pasteUrl.scheme() != serverUrl.scheme() )
    {
        cError() << "Paste server returned an unexpected URL";
        return QString();
    }

    return pasteUrl.toString() + ".log";
}

STATICTEST bool
waitForReply( QNetworkReply* reply, int timeoutMs )
{
    if ( !reply )
    {
        return false;
    }

    if ( reply->isFinished() )
    {
        return true;
    }

    QEventLoop loop;
    QTimer timer;
    timer.setSingleShot( true );
    QObject::connect( reply, &QNetworkReply::finished, &loop, &QEventLoop::quit );
    QObject::connect( &timer, &QTimer::timeout, &loop, &QEventLoop::quit );
    timer.start( timeoutMs );
    loop.exec();

    if ( !reply->isFinished() )
    {
        cError() << "Timed out waiting for paste server response";
        reply->abort();
        return false;
    }

    return true;
}

STATICTEST QString
httpLogUpload( const QByteArray& pasteData, const QUrl& serverUrl, QObject* parent )
{
    constexpr int httpTimeoutMs = 30000;

    QNetworkAccessManager manager( parent );
    QNetworkRequest request( serverUrl );
    request.setAttribute( QNetworkRequest::RedirectPolicyAttribute, QNetworkRequest::NoLessSafeRedirectPolicy );
    request.setHeader( QNetworkRequest::UserAgentHeader, QStringLiteral( "Calamares Paste Upload" ) );
    QNetworkReply* reply = manager.post( request, pasteData );

    if ( !waitForReply( reply, httpTimeoutMs ) )
    {
        reply->deleteLater();
        return QString();
    }

    if ( reply->error() != QNetworkReply::NoError )
    {
        cError() << "Could not upload to paste server" << reply->errorString();
        reply->deleteLater();
        return QString();
    }

    const QString pasteUrl = httpPasteUrl( reply->readAll(), serverUrl );
    reply->deleteLater();
    return pasteUrl;
}

QString
Calamares::Paste::doLogUpload( QObject* parent )
{
    auto [ type, serverUrl, sizeLimitBytes ] = Calamares::Branding::instance()->uploadServer();
    if ( !serverUrl.isValid() )
    {
        cWarning() << "Upload configured with invalid URL";
        return QString();
    }
    if ( type == Calamares::Branding::UploadServerType::None )
    {
        // Early return to avoid reading the log file
        return QString();
    }
    if ( sizeLimitBytes == 0 )
    {
        // Suggests that it is un-set in the config file
        cWarning() << "Upload configured to send 0 bytes";
        return QString();
    }

    QByteArray pasteData = logFileContents( sizeLimitBytes );
    if ( pasteData.isEmpty() )
    {
        // An error has already been logged
        return QString();
    }

    switch ( type )
    {
    case Calamares::Branding::UploadServerType::None:
        cWarning() << "No upload configured.";
        return QString();
    case Calamares::Branding::UploadServerType::Fiche:
        return ficheLogUpload( pasteData, serverUrl, parent );
    case Calamares::Branding::UploadServerType::Http:
        return httpLogUpload( pasteData, serverUrl, parent );
    }
    return QString();
}

QString
Calamares::Paste::doLogUploadUI( QWidget* parent )
{
    // These strings originated in the ViewManager class
    QString pasteUrl = Calamares::Paste::doLogUpload( parent );
    QString pasteUrlMessage;
    if ( pasteUrl.isEmpty() )
    {
        pasteUrlMessage = QCoreApplication::translate( "Calamares::ViewManager",
                                                       "The upload was unsuccessful. No web-paste was done." );
    }
    else
    {
        QClipboard* clipboard = QApplication::clipboard();
        clipboard->setText( pasteUrl, QClipboard::Clipboard );

        if ( clipboard->supportsSelection() )
        {
            clipboard->setText( pasteUrl, QClipboard::Selection );
        }
        QString pasteUrlFmt = QCoreApplication::translate( "Calamares::ViewManager",
                                                           "Install log posted to\n\n%1\n\nLink copied to clipboard" );
        pasteUrlMessage = pasteUrlFmt.arg( pasteUrl );
    }

    QMessageBox mb( QMessageBox::Critical,
                    QCoreApplication::translate( "Calamares::ViewManager", "Install Log Paste URL" ),
                    pasteUrlMessage,
                    QMessageBox::Ok );
    Calamares::fixButtonLabels( &mb );
    mb.exec();
    return pasteUrl;
}

bool
Calamares::Paste::isEnabled()
{
    auto [ type, serverUrl, sizeLimitBytes ] = Calamares::Branding::instance()->uploadServer();
    return type != Calamares::Branding::UploadServerType::None && sizeLimitBytes != 0;
}
