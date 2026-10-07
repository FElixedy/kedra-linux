/* === This file is part of Calamares - <https://calamares.io> ===
 *
 *   SPDX-FileCopyrightText: 2026 Kedra Linux
 *   SPDX-License-Identifier: GPL-3.0-or-later
 *
 *   Calamares is Free Software: see the License-Identifier above.
 */

#include "KedraExperienceViewStep.h"

#include "KedraExperiencePage.h"

#include "GlobalStorage.h"
#include "JobQueue.h"
#include "utils/Logger.h"
#include "utils/Variant.h"

#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonObject>
#include <QProcess>

CALAMARES_PLUGIN_FACTORY_DEFINITION( KedraExperienceViewStepFactory,
                                     registerPlugin< KedraExperienceViewStep >(); )

/// GlobalStorage key the selection is published under, as a JSON string.
/// Consumed by the `kedraexperiencepackages` job.
static const char* GS_SELECTION = "kedraExperienceSelection";

static const int PROFILES_TIMEOUT_MS = 60000;

KedraExperienceViewStep::KedraExperienceViewStep( QObject* parent )
    : Calamares::ViewStep( parent )
    , m_resolver( QStringLiteral( "/usr/bin/kedra-experience" ) )
    , m_basePreset( QStringLiteral( "kedra" ) )
{
}

KedraExperienceViewStep::~KedraExperienceViewStep()
{
    if ( m_widget && m_widget->parent() == nullptr )
    {
        m_widget->deleteLater();
    }
}

QString
KedraExperienceViewStep::prettyName() const
{
    return tr( "Desktop Experience" );
}

QWidget*
KedraExperienceViewStep::widget()
{
    if ( !m_widget )
    {
        m_widget = new KedraExperiencePage( nullptr );
        connect( m_widget,
                 &KedraExperiencePage::selectionChanged,
                 this,
                 [ this ]() { emit nextStatusChanged( isNextEnabled() ); } );
        loadProfiles();
    }
    return m_widget;
}

void
KedraExperienceViewStep::loadProfiles()
{
    // Fail closed, loudly, and on the page itself. Every path out of this
    // function that does not end in a usable page leaves the step unable to be
    // left, so an installation cannot quietly continue as Vanilla because the
    // resolver was missing.
    if ( !QFileInfo::exists( m_resolver ) )
    {
        cWarning() << "Kedra Experience resolver not found at" << m_resolver;
        m_widget->setError( tr( "The Kedra Experience resolver was not found at %1.\n\n"
                                "This program is installed by the "
                                "kedra-experience-runtime package; the installation "
                                "medium appears to be incomplete." )
                                .arg( m_resolver ) );
        return;
    }

    QProcess process;
    process.start( m_resolver, { QStringLiteral( "profiles" ), QStringLiteral( "--format" ),
                                 QStringLiteral( "json" ) } );
    if ( !process.waitForFinished( PROFILES_TIMEOUT_MS ) )
    {
        cWarning() << "Kedra Experience resolver did not finish";
        m_widget->setError(
            tr( "The Kedra Experience resolver did not respond, so the available "
                "desktop profiles could not be listed." ) );
        return;
    }
    if ( process.exitStatus() != QProcess::NormalExit || process.exitCode() != 0 )
    {
        const QString errors = QString::fromUtf8( process.readAllStandardError() ).trimmed();
        cWarning() << "Kedra Experience resolver failed:" << errors;
        m_widget->setError( tr( "The available desktop profiles could not be listed.\n\n%1" )
                                .arg( errors.isEmpty() ? tr( "No further detail was given." )
                                                       : errors ) );
        return;
    }

    QJsonParseError parseError;
    const QJsonDocument document
        = QJsonDocument::fromJson( process.readAllStandardOutput(), &parseError );
    if ( parseError.error != QJsonParseError::NoError || !document.isObject() )
    {
        cWarning() << "Kedra Experience profiles are not valid JSON:" << parseError.errorString();
        m_widget->setError(
            tr( "The Kedra Experience metadata could not be read: %1" ).arg( parseError.errorString() ) );
        return;
    }

    const QJsonObject profiles = document.object();
    const int version = profiles.value( QStringLiteral( "profiles_schema_version" ) ).toInt( -1 );
    if ( version != m_profilesSchemaVersion )
    {
        // A newer resolver may mean something different by the fields this page
        // reads. Refusing beats rendering a checklist from a shape nobody
        // designed for.
        cWarning() << "Kedra Experience profiles schema" << version << "!= expected"
                   << m_profilesSchemaVersion;
        m_widget->setError( tr( "The Kedra Experience metadata is version %1, which this "
                                "installer does not understand (it expects %2)." )
                                .arg( version )
                                .arg( m_profilesSchemaVersion ) );
        return;
    }

    m_loaded = m_widget->setProfiles( profiles, m_basePreset );
    emit nextStatusChanged( isNextEnabled() );
}

bool
KedraExperienceViewStep::isNextEnabled() const
{
    // Not merely "a choice was made": the page must have been built from
    // metadata this installer understood.
    return m_loaded && m_widget && m_widget->isReady()
        && !m_widget->selection().isEmpty();
}

bool
KedraExperienceViewStep::isBackEnabled() const
{
    return true;
}

bool
KedraExperienceViewStep::isAtBeginning() const
{
    return true;
}

bool
KedraExperienceViewStep::isAtEnd() const
{
    return true;
}

void
KedraExperienceViewStep::onLeave()
{
    if ( !m_widget )
    {
        return;
    }
    const QJsonObject selection = m_widget->selection();
    if ( selection.isEmpty() )
    {
        return;
    }

    // Published as a JSON string, which is what the resolver reads on stdin.
    // Nothing is interpreted here -- not the packages, not the modules, not
    // whether the combination is legal.
    const QString encoded
        = QString::fromUtf8( QJsonDocument( selection ).toJson( QJsonDocument::Compact ) );
    Calamares::JobQueue::instance()->globalStorage()->insert( GS_SELECTION, encoded );
    cDebug() << "Kedra Experience selection:" << encoded;
}

Calamares::JobList
KedraExperienceViewStep::jobs() const
{
    // None. The work is done by the kedraexperiencepackages and
    // kedraexperiencestate job modules, which sit at the right points of the
    // exec sequence rather than wherever this page happens to be.
    return Calamares::JobList();
}

void
KedraExperienceViewStep::setConfigurationMap( const QVariantMap& configurationMap )
{
    m_resolver = Calamares::getString( configurationMap,
                                       QStringLiteral( "resolver" ),
                                       QStringLiteral( "/usr/bin/kedra-experience" ) );
    m_basePreset = Calamares::getString( configurationMap,
                                         QStringLiteral( "basePreset" ),
                                         QStringLiteral( "kedra" ) );
    m_profilesSchemaVersion
        = Calamares::getInteger( configurationMap, QStringLiteral( "profilesSchemaVersion" ), 1 );
}
