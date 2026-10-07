/* === This file is part of Calamares - <https://calamares.io> ===
 *
 *   SPDX-FileCopyrightText: 2026 Kedra Linux
 *   SPDX-License-Identifier: GPL-3.0-or-later
 *
 *   Calamares is Free Software: see the License-Identifier above.
 */

#include "KedraExperiencePage.h"

#include "utils/Logger.h"

#include <QButtonGroup>
#include <QCheckBox>
#include <QGroupBox>
#include <QJsonArray>
#include <QJsonValue>
#include <QLabel>
#include <QRadioButton>
#include <QVBoxLayout>

KedraExperiencePage::KedraExperiencePage( QWidget* parent )
    : QWidget( parent )
    , m_layout( new QVBoxLayout( this ) )
    , m_status( new QLabel( this ) )
    , m_products( new QButtonGroup( this ) )
{
    m_status->setWordWrap( true );
    m_status->setTextFormat( Qt::PlainText );
    m_layout->addWidget( m_status );

    m_productArea = new QWidget( this );
    m_productArea->setLayout( new QVBoxLayout( m_productArea ) );
    m_layout->addWidget( m_productArea );

    m_moduleArea = new QGroupBox( tr( "Modules" ), this );
    m_moduleArea->setLayout( new QVBoxLayout( m_moduleArea ) );
    m_moduleArea->setVisible( false );
    m_layout->addWidget( m_moduleArea );

    m_layout->addStretch( 1 );
}

void
KedraExperiencePage::setError( const QString& text )
{
    m_ready = false;
    m_productArea->setVisible( false );
    m_moduleArea->setVisible( false );
    m_status->setText( text );
    emit selectionChanged();
}

bool
KedraExperiencePage::setProfiles( const QJsonObject& profiles, const QString& basePreset )
{
    m_basePreset = basePreset;

    const QJsonArray presets = profiles.value( QStringLiteral( "presets" ) ).toArray();
    if ( presets.isEmpty() )
    {
        setError( tr( "The Kedra Experience metadata describes no profiles. The "
                      "installation medium is incomplete." ) );
        return false;
    }

    // One radio per user-selectable preset, in the order the resolver listed
    // them. `user_selectable` is honoured, never inferred: a preset the metadata
    // says must not be offered is not offered.
    auto* productLayout = qobject_cast< QVBoxLayout* >( m_productArea->layout() );
    QJsonObject baseObject;
    for ( const auto& value : presets )
    {
        const QJsonObject preset = value.toObject();
        if ( !preset.value( QStringLiteral( "user_selectable" ) ).toBool() )
        {
            continue;
        }
        const QString id = preset.value( QStringLiteral( "id" ) ).toString();
        if ( id.isEmpty() )
        {
            continue;
        }
        if ( id == basePreset )
        {
            baseObject = preset;
        }

        auto* button = new QRadioButton(
            preset.value( QStringLiteral( "name" ) ).toString( id ), m_productArea );
        button->setToolTip( preset.value( QStringLiteral( "description" ) ).toString() );
        m_products->addButton( button );
        productLayout->addWidget( button );
        m_productPresets.append( qMakePair( button, id ) );
        connect( button, &QRadioButton::toggled, this, [ this ]() {
            rebuildModuleVisibility();
            emit selectionChanged();
        } );
    }

    if ( m_productPresets.isEmpty() )
    {
        setError( tr( "No Kedra Experience profile may be offered for installation. "
                      "The installation medium is incomplete." ) );
        return false;
    }

    // A basePreset the metadata does not have is a CONFIGURATION ERROR, and it
    // fails loudly here rather than quietly dropping the Custom option.
    //
    // Hiding it would leave Vanilla and Kedra working, so the installer would
    // look correct while silently offering two products instead of three --
    // discoverable only by someone who knew a third was meant to exist. A
    // misconfigured installer must not be able to ship a smaller product range
    // than it was built to offer.
    if ( baseObject.isEmpty() )
    {
        setError( tr( "Installer configuration error: basePreset is set to \"%1\", "
                      "which the Kedra Experience metadata does not define.\n\n"
                      "Custom profiles cannot be offered, so installation cannot "
                      "continue. Correct basePreset in kedraexperience.conf." )
                      .arg( basePreset ) );
        return false;
    }

    // Custom is this installer's own product framing -- "the base preset, with
    // changes" -- rather than a preset in the metadata. Its module list is
    // entirely the resolver's.
    const QJsonArray modules = baseObject.value( QStringLiteral( "modules" ) ).toArray();
    auto* moduleLayout = qobject_cast< QVBoxLayout* >( m_moduleArea->layout() );
    for ( const auto& value : modules )
    {
        const QJsonObject module = value.toObject();
        // The one rule the page enforces, and it comes from the data: a module
        // the metadata does not mark selectable is never shown.
        if ( !module.value( QStringLiteral( "user_selectable" ) ).toBool() )
        {
            continue;
        }
        KedraModule entry;
        entry.id = module.value( QStringLiteral( "id" ) ).toString();
        if ( entry.id.isEmpty() )
        {
            continue;
        }
        entry.name = module.value( QStringLiteral( "name" ) ).toString( entry.id );
        entry.description = module.value( QStringLiteral( "description" ) ).toString();
        entry.defaultEnabled = module.value( QStringLiteral( "enabled" ) ).toBool();

        auto* box = new QCheckBox( entry.name, m_moduleArea );
        box->setToolTip( entry.description );
        box->setChecked( entry.defaultEnabled );
        moduleLayout->addWidget( box );
        m_moduleBoxes.append( qMakePair( box, entry ) );
        connect( box, &QCheckBox::toggled, this, &KedraExperiencePage::selectionChanged );
    }

    if ( m_moduleBoxes.isEmpty() )
    {
        // The base preset exists but offers nothing a user may change. Also a
        // configuration or metadata error, and refused for the same reason.
        setError( tr( "Installer configuration error: the \"%1\" profile offers no "
                      "user-selectable modules, so a Custom profile cannot be "
                      "built.\n\nInstallation cannot continue." )
                      .arg( basePreset ) );
        return false;
    }

    m_customButton = new QRadioButton( tr( "Custom Kedra Experience" ), m_productArea );
    m_customButton->setToolTip(
        tr( "The Kedra Experience, with individual parts switched off." ) );
    m_products->addButton( m_customButton );
    productLayout->addWidget( m_customButton );
    connect( m_customButton, &QRadioButton::toggled, this, [ this ]() {
        rebuildModuleVisibility();
        emit selectionChanged();
    } );

    // Default to the base preset -- the recommended product -- rather than to
    // whatever happened to be listed first. It is necessarily present: a
    // basePreset absent from the offered set was refused above.
    for ( const auto& pair : m_productPresets )
    {
        if ( pair.second == basePreset )
        {
            pair.first->setChecked( true );
            break;
        }
    }

    m_productArea->setVisible( true );
    m_status->setText( tr( "Choose how much of the Kedra desktop experience to install." ) );
    m_ready = true;
    rebuildModuleVisibility();
    emit selectionChanged();
    return true;
}

void
KedraExperiencePage::rebuildModuleVisibility()
{
    const bool custom = m_customButton && m_customButton->isChecked();
    m_moduleArea->setVisible( custom );
}

QJsonObject
KedraExperiencePage::selection() const
{
    QJsonObject document;
    if ( !m_ready )
    {
        return document;
    }

    if ( m_customButton && m_customButton->isChecked() )
    {
        document.insert( QStringLiteral( "base_preset" ), m_basePreset );
        QJsonArray overrides;
        for ( const auto& pair : m_moduleBoxes )
        {
            // Only deltas. A module left at the preset's own answer contributes
            // nothing, which is what keeps a Custom profile a pointer plus
            // changes rather than a copy of the module list.
            if ( pair.first->isChecked() == pair.second.defaultEnabled )
            {
                continue;
            }
            QJsonObject entry;
            entry.insert( QStringLiteral( "id" ), pair.second.id );
            entry.insert( QStringLiteral( "enabled" ), pair.first->isChecked() );
            overrides.append( entry );
        }
        document.insert( QStringLiteral( "module_overrides" ), overrides );
        return document;
    }

    for ( const auto& pair : m_productPresets )
    {
        if ( pair.first->isChecked() )
        {
            document.insert( QStringLiteral( "base_preset" ), pair.second );
            document.insert( QStringLiteral( "module_overrides" ), QJsonArray() );
            return document;
        }
    }
    return document;
}
