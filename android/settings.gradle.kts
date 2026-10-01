pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}
dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        // Google's copy of Maven Central first: Maven Central itself often answers "too many requests"
        maven("https://maven-central.storage-download.googleapis.com/maven2")
        mavenCentral()
    }
}
rootProject.name = "Armonico"
include(":app")
